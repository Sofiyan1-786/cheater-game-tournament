from flask import Flask, render_template, request, redirect, url_for
import psycopg2
import subprocess
import os
import numpy as np
import traceback

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"

def get_connection():
    conn = psycopg2.connect(
        database=os.environ["db.database"],
        user=os.environ["db.user"],
        password=os.environ["db.password"],
        host=os.environ["db.host"],
        port=os.environ["db.port"],
    )
    print(conn.closed)
    return conn

def get_agents():
    return [agent for agent in os.listdir(UPLOAD_FOLDER) if agent.endswith(".py")]

def get_ranking():
    conn = get_connection()
    scores = []
    indexes = {}
    print(f"Is connection closed: {conn.closed}")
    with conn.cursor() as c:
        c.execute(
            "SELECT * FROM scores"
        )
        scores = c.fetchall()
        c.execute(
            "SELECT id, name FROM agents"
        )
        indexes = {name: id for id, name in c.fetchall()}

    conn.commit()
    conn.close()

    if not indexes:
        return []

    size = max(indexes.values()) + 1

    matrix = np.zeros((size, size), dtype=np.int32)

    for id1, id2, score in scores:
        matrix[id1, id2] = score
        matrix[id2, id1] = -score

    ranking = [(name, sum(matrix[id])) for name, id in indexes.items()]

    ranking.sort(key=lambda x: x[1], reverse=True)

    return ranking

@app.route("/")
def index():
    ranking = get_ranking()

    return render_template("index.html", ranking=ranking)

@app.route("/upload", methods=["POST"])
def upload():
    file = request.files["agent"]

    agent = file.filename

    if not agent:
        raise Exception("Invalid file name")

    if not agent.endswith(".py"):
        raise Exception("Only .py files are allowed")

    safe_name = os.path.basename(agent)
    if agent != safe_name:
        raise Exception("Invalid file name")
    
    agent = safe_name
    safe_name = os.path.basename(agent)
    if agent != safe_name:
        raise Exception("Invalid file name")

    if agent == "randomPlayer.py":
        raise Exception("Illegal file name")

    MAX_SIZE = 1024 * 1024
    raw = file.read(MAX_SIZE + 1)
    if len(raw) > MAX_SIZE:
        raise Exception("File too large (max 1 MB)")

    try:
        agent_code = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Exception("File must be valid UTF-8 text")

    path1 = os.path.join(UPLOAD_FOLDER, agent)

    conn = get_connection()

    agents = {}
    
    with conn.cursor() as c:

        c.execute("SELECT * FROM agents")
        agents_data = c.fetchall()

    for id, name, code in agents_data:
        path = os.path.join(UPLOAD_FOLDER, name)
        with open(path, "w") as f:
            f.write(code)

        agents[name] = id

    with open(path1, "w") as f:
        f.write(agent_code)

    scores = []

    for other_agent in agents.keys():
        if other_agent == agent: continue
        path2 = os.path.join(UPLOAD_FOLDER, other_agent)
        print("Calculating score for " + agent + " vs " + other_agent)
        result = subprocess.run(
            ["python", "game/evaluator.py", path1, path2],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            raise Exception(f"Error occurred while evaluating {agent} vs {other_agent}: {result.stdout + result.stderr}")

        result = int(result.stdout)

        scores.append(
            (agent, other_agent, int(result)) if agent < other_agent else
            (other_agent, agent, -int(result))
        )

    with conn.cursor() as c:
        c.execute("INSERT INTO agents (name, code) \
            VALUES (%(name)s, %(code)s) \
            ON CONFLICT(name) \
            DO UPDATE SET code = excluded.code;",
            {"name": agent, "code": agent_code}
        )

    conn.commit()

    with conn.cursor() as c:
        c.execute("SELECT id FROM agents WHERE name = %(agent)s;", {"agent": agent})
        id = c.fetchone()[0]

        agents[agent] = id
        
        for agent1, agent2, score in scores:
            c.execute(
                "INSERT INTO scores (agent1, agent2, score) \
                VALUES (%(agent1)s, %(agent2)s, %(score)s) \
                ON CONFLICT(agent1, agent2) \
                DO UPDATE SET score = excluded.score;",
                {"agent1": agents[agent1], "agent2": agents[agent2], "score": score}
            )

    conn.commit()
    conn.close()

    return redirect(url_for("index"))

@app.errorhandler(Exception)
def handle_exception(e):
    return f"""
    <h1>Error</h1>
    <p>{e}</p>
    """, 400