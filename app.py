from flask import Flask, request, render_template, redirect, url_for, flash, session
from pymongo import MongoClient, errors
from datetime import datetime
from functools import wraps
import atexit

app = Flask(__name__)
app.secret_key = "your_secret_key"

# -------------------- MongoDB Setup --------------------
MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "Voting_s"

try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    client.server_info()
    db = client[DB_NAME]
    candidates_col = db["candidates"]
    voters_col = db["voters"]
    logs_col = db["vote_logs"]
    election_history_col = db["election_history"]
    print("✅ Connected to MongoDB successfully.")
except errors.ServerSelectionTimeoutError as err:
    print("❌ Failed to connect to MongoDB:", err)
    db = None

# -------------------- Shutdown Hook --------------------
def save_and_reset():
    print("\n⚠️ Ctrl+C detected — Saving results and resetting data...")
    try:
        all_candidates = list(candidates_col.find({}, {"_id": 0}))
        result_summary = {c["name"]: c["votes"] for c in all_candidates}
        winner = max(result_summary, key=result_summary.get) if result_summary else "No candidates"
        vote_logs = list(logs_col.find({}, {"_id": 0}))

        election_history_col.insert_one({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "votes": result_summary,
            "winner": winner,
            "Voter_logs": vote_logs
        })

        candidates_col.update_many({}, {"$set": {"votes": 0}})
        voters_col.update_many({}, {"$set": {"has_voted": False}})
        logs_col.delete_many({})
        print("✅ Data saved and reset.")
    except Exception as e:
        print("❌ Error during shutdown:", e)

atexit.register(save_and_reset)

# -------------------- Login Required Decorator --------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

# -------------------- Routes --------------------
@app.route('/')
def index():
    if db is None:
        return "Database connection failed. Please check MongoDB.", 500
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    voter_id = request.form['voter_id']
    password = request.form['password']
    voter = voters_col.find_one({"id": voter_id, "password": password})
    if voter:
        if voter.get("has_voted", False):
            flash("You have already voted!")
            return redirect(url_for('index'))
        all_candidates = list(candidates_col.find({}, {"_id": 0}))
        return render_template('vote.html', candidates=all_candidates, voter_id=voter_id)
    flash("Invalid credentials!")
    return redirect(url_for('index'))

@app.route('/vote', methods=['POST'])
def vote():
    voter_id = request.form['voter_id']
    candidate_id = int(request.form['candidate_id'])

    voter = voters_col.find_one({"id": voter_id})
    candidate = candidates_col.find_one({"id": candidate_id})

    if voter and candidate and not voter.get("has_voted", False):
        voters_col.update_one({"id": voter_id}, {"$set": {"has_voted": True}})
        candidates_col.update_one({"id": candidate_id}, {"$inc": {"votes": 1}})
        logs_col.insert_one({
            "voter_id": voter_id,
            "candidate_id": candidate_id,
            "candidate_name": candidate["name"]
        })
        flash("Vote recorded successfully!")
    else:
        flash("Invalid vote or you have already voted.")
    return redirect(url_for('index'))

# -------------------- Admin --------------------
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"

@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        flash("Invalid admin credentials!")
    return render_template('admin_login.html')
    

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))

@app.route("/admin/dashboard")
@login_required
def admin_dashboard():
    all_voters = list(voters_col.find({}, {"_id": 0}))
    all_candidates = list(candidates_col.find({}, {"_id": 0}))
    all_votes = list(logs_col.find({}, {"_id": 0}))

    candidate_votes = {c["id"]: 0 for c in all_candidates}

    for vote in all_votes:
        cid = vote.get('candidate_id')
        if cid in candidate_votes:
            candidate_votes[cid] += 1

    if candidate_votes:
        total_votes = sum(candidate_votes.values())
        if total_votes == 0:
            winner_name = "-"
        else:
            winner_id = max(candidate_votes, key=candidate_votes.get)
            winner_name = next((c['name'] for c in all_candidates if c['id'] == winner_id), "Unknown")

        all_results = [{
            "winner": winner_name,
            "votes": candidate_votes
        }]
    else:
        all_results = [{
            "winner": "-",
            "votes": {}
        }]

    return render_template("admin_dashboard.html", voters=all_voters, candidates=all_candidates, results=all_results)

# -------------------- Voter Management --------------------
@app.route('/admin/voter/add', methods=['POST'])
@login_required
def add_voter():
    voters_col.insert_one({
        "id": request.form['voter_id'],
        "password": request.form['password'],
        "has_voted": False
    })
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/voter/delete/<voter_id>')
@login_required
def delete_voter(voter_id):
    voters_col.delete_one({"id": voter_id})
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/voter/update', methods=['POST'])
@login_required
def update_voter():
    voters_col.update_one({"id": request.form['old_id']}, {
        "$set": {
            "id": request.form['new_id'],
            "password": request.form['new_password']
        }
    })
    return redirect(url_for('admin_dashboard'))

# -------------------- Candidate Management --------------------
@app.route('/admin/candidate/add', methods=['POST'])
@login_required
def add_candidate():
    candidates_col.insert_one({
        "id": int(request.form['candidate_id']),
        "name": request.form['candidate_name'],
        "votes": 0
    })
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/candidate/delete/<int:candidate_id>')
@login_required
def delete_candidate(candidate_id):
    candidates_col.delete_one({"id": candidate_id})
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/candidate/update', methods=['POST'])
@login_required
def update_candidate():
    candidates_col.update_one({"id": int(request.form['old_id'])}, {
        "$set": {"name": request.form['new_name']}
    })
    return redirect(url_for('admin_dashboard'))

# -------------------- Run Server --------------------
if __name__ == "__main__":
    app.run(host='127.0.0.1', port=5000, ssl_context=('server_cert.pem', 'server_key.pem'))
