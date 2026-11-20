import os
import re
import sys
from datetime import datetime, date
from functools import wraps

from bson import ObjectId
from bson.errors import InvalidId
from dotenv import load_dotenv
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, session, abort)
from pymongo import MongoClient, ReturnDocument
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-only-change-me")
CLINIC_NAME = os.getenv("CLINIC_NAME", "City Health Clinic")

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"),
                     serverSelectionTimeoutMS=8000)
try:
    client.admin.command("ping")
except Exception as e:
    print("\nCould not connect to MongoDB:", str(e)[:300])
    print("Delete the .env file and run run.bat again to re-enter your Atlas string.\n")
    sys.exit(1)

db = client[os.getenv("DB_NAME", "clinic")]
users = db["users"]
patients = db["patients"]
appointments = db["appointments"]
visits = db["visits"]
invoices = db["invoices"]
inventory = db["inventory"]
counters = db["counters"]

users.create_index("username", unique=True)
if users.count_documents({}) == 0:
    users.insert_one({"username": "admin", "name": "Administrator", "role": "admin",
                      "password": generate_password_hash(os.getenv("ADMIN_PASSWORD", "admin123"))})
    print(">>> First run: created user 'admin'")


def oid(v):
    try:
        return ObjectId(v)
    except (InvalidId, TypeError):
        abort(404)


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def to_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if "user" not in session:
            return redirect(url_for("login", next=request.path))
        return f(*a, **k)
    return w


def roles(*allowed):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            if "user" not in session:
                return redirect(url_for("login", next=request.path))
            if session["user"]["role"] not in allowed:
                abort(403)
            return f(*a, **k)
        return w
    return deco


def next_invoice_no():
    c = counters.find_one_and_update({"_id": "invoice"}, {"$inc": {"seq": 1}},
                                     upsert=True, return_document=ReturnDocument.AFTER)
    return f"INV-{c['seq']:05d}"


@app.context_processor
def inject():
    return {"clinic_name": CLINIC_NAME}


@app.template_filter("age")
def age_filter(dob):
    try:
        d = datetime.strptime(dob, "%Y-%m-%d").date()
        t = date.today()
        return t.year - d.year - ((t.month, t.day) < (d.month, d.day))
    except Exception:
        return ""


@app.template_filter("money")
def money_filter(v):
    return f"{(v or 0):,.2f}"


@app.errorhandler(403)
def e403(e):
    return render_template("error.html", code=403, msg="You don't have permission to do that."), 403


@app.errorhandler(404)
def e404(e):
    return render_template("error.html", code=404, msg="Page not found."), 404


# ---------- auth ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = users.find_one({"username": request.form["username"].strip().lower()})
        if u and check_password_hash(u["password"], request.form["password"]):
            session["user"] = {"id": str(u["_id"]), "username": u["username"],
                               "name": u["name"], "role": u["role"]}
            nxt = request.args.get("next", "")
            if nxt.startswith("/") and not nxt.startswith("//"):
                return redirect(nxt)
            return redirect(url_for("dashboard"))
        flash("Wrong username or password.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        u = users.find_one({"_id": ObjectId(session["user"]["id"])})
        if not check_password_hash(u["password"], request.form["old"]):
            flash("Current password is wrong.")
        elif len(request.form["new"]) < 6:
            flash("New password must be at least 6 characters.")
        else:
            users.update_one({"_id": u["_id"]},
                             {"$set": {"password": generate_password_hash(request.form["new"])}})
            flash("Password changed.")
            return redirect(url_for("dashboard"))
    return render_template("password.html")


# ---------- dashboard ----------
@app.route("/")
@login_required
def dashboard():
    today = date.today().isoformat()
    outstanding = sum(i["total"] - i["paid"] for i in invoices.find({"status": {"$ne": "paid"}}))
    revenue_today = 0.0
    for i in invoices.find({"payments.date": today}):
        revenue_today += sum(p["amount"] for p in i["payments"] if p["date"] == today)
    return render_template(
        "dashboard.html",
        n_patients=patients.count_documents({}),
        today_appts=list(appointments.find({"date": today, "status": {"$ne": "cancelled"}}).sort("time", 1)),
        outstanding=outstanding,
        revenue_today=revenue_today,
        low_stock=list(inventory.find({"$expr": {"$lte": ["$qty", "$reorder"]}}).sort("name", 1)),
        today=today,
    )


# ---------- patients ----------
PATIENT_FIELDS = ["name", "dob", "gender", "phone", "email", "address",
                  "blood_group", "allergies", "emergency_contact", "notes"]


def patient_data(form):
    return {f: form.get(f, "").strip() for f in PATIENT_FIELDS}


@app.route("/patients")
@login_required
def patients_list():
    q = request.args.get("q", "").strip()
    query = {}
    if q:
        rx = {"$regex": re.escape(q), "$options": "i"}
        query = {"$or": [{"name": rx}, {"phone": rx}]}
    return render_template("patients.html",
                           patients=list(patients.find(query).sort("name", 1)), q=q)


@app.route("/patients/new", methods=["GET", "POST"])
@roles("admin", "doctor", "receptionist")
def patient_new():
    if request.method == "POST":
        data = patient_data(request.form)
        if not data["name"]:
            flash("Name is required.")
            return render_template("patient_form.html", p=data)
        data["created_at"] = datetime.now().strftime("%Y-%m-%d")
        r = patients.insert_one(data)
        flash("Patient registered.")
        return redirect(url_for("patient_detail", pid=r.inserted_id))
    return render_template("patient_form.html", p=None)


@app.route("/patients/<pid>")
@login_required
def patient_detail(pid):
    i = oid(pid)
    p = patients.find_one({"_id": i}) or abort(404)
    can_clinical = session["user"]["role"] in ("admin", "doctor")
    return render_template(
        "patient_detail.html", p=p, can_clinical=can_clinical,
        visits=list(visits.find({"patient_id": i}).sort("date", -1)) if can_clinical else [],
        appts=list(appointments.find({"patient_id": i}).sort([("date", -1), ("time", -1)])),
        invs=list(invoices.find({"patient_id": i}).sort("created_at", -1)),
    )


@app.route("/patients/<pid>/edit", methods=["GET", "POST"])
@roles("admin", "doctor", "receptionist")
def patient_edit(pid):
    i = oid(pid)
    p = patients.find_one({"_id": i}) or abort(404)
    if request.method == "POST":
        data = patient_data(request.form)
        if not data["name"]:
            flash("Name is required.")
        else:
            patients.update_one({"_id": i}, {"$set": data})
            flash("Patient updated.")
            return redirect(url_for("patient_detail", pid=pid))
    return render_template("patient_form.html", p=p)


@app.route("/patients/<pid>/delete", methods=["POST"])
@roles("admin")
def patient_delete(pid):
    i = oid(pid)
    patients.delete_one({"_id": i})
    appointments.delete_many({"patient_id": i})
    visits.delete_many({"patient_id": i})
    invoices.delete_many({"patient_id": i})
    flash("Patient and all their records deleted.")
    return redirect(url_for("patients_list"))


@app.route("/patients/<pid>/visits", methods=["POST"])
@roles("admin", "doctor")
def visit_add(pid):
    i = oid(pid)
    f = request.form
    visits.insert_one({
        "patient_id": i,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "doctor_name": session["user"]["name"],
        "complaint": f.get("complaint", "").strip(),
        "bp": f.get("bp", "").strip(),
        "temp": f.get("temp", "").strip(),
        "weight": f.get("weight", "").strip(),
        "diagnosis": f.get("diagnosis", "").strip(),
        "treatment": f.get("treatment", "").strip(),
        "prescription": f.get("prescription", "").strip(),
    })
    flash("Visit recorded.")
    return redirect(url_for("patient_detail", pid=pid))


@app.route("/visits/<vid>/delete", methods=["POST"])
@roles("admin")
def visit_delete(vid):
    v = visits.find_one({"_id": oid(vid)}) or abort(404)
    visits.delete_one({"_id": v["_id"]})
    flash("Visit deleted.")
    return redirect(url_for("patient_detail", pid=v["patient_id"]))


# ---------- appointments ----------
STATUSES = ["scheduled", "completed", "cancelled", "no-show"]


@app.route("/appointments")
@login_required
def appointments_list():
    d = request.args.get("date", "").strip()
    doc = request.args.get("doctor", "").strip()
    query = {}
    if d:
        query["date"] = d
    if doc:
        query["doctor_id"] = oid(doc)
    return render_template(
        "appointments.html",
        appts=list(appointments.find(query).sort([("date", 1), ("time", 1)])),
        pts=list(patients.find().sort("name", 1)),
        doctors=list(users.find({"role": "doctor"}).sort("name", 1)),
        statuses=STATUSES, d=d, doc=doc,
        selected=request.args.get("patient", ""),
    )


@app.route("/appointments/add", methods=["POST"])
@roles("admin", "doctor", "receptionist")
def appointment_add():
    f = request.form
    p = patients.find_one({"_id": oid(f["patient_id"])})
    doc = users.find_one({"_id": oid(f["doctor_id"]), "role": "doctor"})
    if not p or not doc:
        flash("Choose a valid patient and doctor.")
        return redirect(url_for("appointments_list"))
    clash = appointments.find_one({"doctor_id": doc["_id"], "date": f["date"],
                                   "time": f["time"], "status": {"$ne": "cancelled"}})
    if clash:
        flash(f"Dr. {doc['name']} already has an appointment at {f['time']} on {f['date']}.")
        return redirect(url_for("appointments_list"))
    appointments.insert_one({
        "patient_id": p["_id"], "patient_name": p["name"],
        "doctor_id": doc["_id"], "doctor_name": doc["name"],
        "date": f["date"], "time": f["time"],
        "reason": f.get("reason", "").strip(), "status": "scheduled",
    })
    flash("Appointment booked.")
    return redirect(url_for("appointments_list", date=f["date"]))


@app.route("/appointments/<aid>/status", methods=["POST"])
@roles("admin", "doctor", "receptionist")
def appointment_status(aid):
    s = request.form.get("status")
    if s in STATUSES:
        appointments.update_one({"_id": oid(aid)}, {"$set": {"status": s}})
        flash(f"Appointment marked {s}.")
    return redirect(request.referrer or url_for("appointments_list"))


@app.route("/appointments/<aid>/delete", methods=["POST"])
@roles("admin", "receptionist")
def appointment_delete(aid):
    appointments.delete_one({"_id": oid(aid)})
    flash("Appointment deleted.")
    return redirect(request.referrer or url_for("appointments_list"))


# ---------- billing ----------
@app.route("/invoices")
@roles("admin", "receptionist")
def invoices_list():
    status = request.args.get("status", "")
    query = {"status": status} if status in ("unpaid", "partial", "paid") else {}
    return render_template("invoices.html",
                           invs=list(invoices.find(query).sort("created_at", -1)), status=status)


@app.route("/invoices/new", methods=["GET", "POST"])
@roles("admin", "receptionist")
def invoice_new():
    if request.method == "POST":
        p = patients.find_one({"_id": oid(request.form["patient_id"])})
        items = [{"desc": d.strip(), "amount": round(num(a), 2)}
                 for d, a in zip(request.form.getlist("desc"), request.form.getlist("amt"))
                 if d.strip() and num(a) > 0]
        if not p or not items:
            flash("Choose a patient and add at least one item with an amount.")
        else:
            r = invoices.insert_one({
                "number": next_invoice_no(),
                "patient_id": p["_id"], "patient_name": p["name"],
                "items": items, "total": round(sum(x["amount"] for x in items), 2),
                "paid": 0.0, "payments": [], "status": "unpaid",
                "created_at": datetime.now().strftime("%Y-%m-%d"),
            })
            flash("Invoice created.")
            return redirect(url_for("invoice_view", iid=r.inserted_id))
    return render_template("invoice_new.html", pts=list(patients.find().sort("name", 1)),
                           selected=request.args.get("patient", ""))


@app.route("/invoices/<iid>")
@roles("admin", "receptionist")
def invoice_view(iid):
    inv = invoices.find_one({"_id": oid(iid)}) or abort(404)
    pt = patients.find_one({"_id": inv["patient_id"]}) or {}
    return render_template("invoice_view.html", inv=inv, pt=pt)


@app.route("/invoices/<iid>/pay", methods=["POST"])
@roles("admin", "receptionist")
def invoice_pay(iid):
    inv = invoices.find_one({"_id": oid(iid)}) or abort(404)
    balance = round(inv["total"] - inv["paid"], 2)
    amt = min(round(num(request.form.get("amount")), 2), balance)
    if amt <= 0:
        flash("Enter a valid amount.")
    else:
        new_paid = round(inv["paid"] + amt, 2)
        invoices.update_one({"_id": inv["_id"]}, {
            "$set": {"paid": new_paid, "status": "paid" if new_paid >= inv["total"] else "partial"},
            "$push": {"payments": {"amount": amt, "method": request.form.get("method", "cash"),
                                   "date": date.today().isoformat(),
                                   "by": session["user"]["name"]}}})
        flash("Payment recorded.")
    return redirect(url_for("invoice_view", iid=iid))


@app.route("/invoices/<iid>/delete", methods=["POST"])
@roles("admin")
def invoice_delete(iid):
    invoices.delete_one({"_id": oid(iid)})
    flash("Invoice deleted.")
    return redirect(url_for("invoices_list"))


# ---------- pharmacy / inventory ----------
@app.route("/inventory")
@roles("admin", "doctor", "receptionist")
def inventory_list():
    q = request.args.get("q", "").strip()
    query = {"name": {"$regex": re.escape(q), "$options": "i"}} if q else {}
    return render_template("inventory.html", items=list(inventory.find(query).sort("name", 1)), q=q)


@app.route("/inventory/add", methods=["POST"])
@roles("admin", "receptionist")
def inventory_add():
    f = request.form
    if not f.get("name", "").strip():
        flash("Item name is required.")
    else:
        inventory.insert_one({"name": f["name"].strip(), "category": f.get("category", "").strip(),
                              "qty": to_int(f.get("qty")), "reorder": to_int(f.get("reorder"), 10),
                              "price": round(num(f.get("price")), 2)})
        flash("Item added.")
    return redirect(url_for("inventory_list"))


@app.route("/inventory/<iid>/adjust", methods=["POST"])
@roles("admin", "doctor", "receptionist")
def inventory_adjust(iid):
    delta = to_int(request.form.get("delta"))
    flt = {"_id": oid(iid)}
    if delta < 0:
        flt["qty"] = {"$gte": -delta}
    r = inventory.update_one(flt, {"$inc": {"qty": delta}})
    flash("Stock updated." if r.modified_count else "Not enough stock for that change.")
    return redirect(url_for("inventory_list"))


@app.route("/inventory/<iid>/delete", methods=["POST"])
@roles("admin")
def inventory_delete(iid):
    inventory.delete_one({"_id": oid(iid)})
    flash("Item deleted.")
    return redirect(url_for("inventory_list"))


# ---------- staff (admin) ----------
@app.route("/staff")
@roles("admin")
def staff_list():
    return render_template("staff.html", staff=list(users.find().sort("name", 1)))


@app.route("/staff/add", methods=["POST"])
@roles("admin")
def staff_add():
    f = request.form
    username = f.get("username", "").strip().lower()
    if not username or not f.get("name", "").strip() or len(f.get("password", "")) < 6:
        flash("Username, name and a password of 6+ characters are required.")
    elif f.get("role") not in ("admin", "doctor", "receptionist"):
        flash("Invalid role.")
    elif users.find_one({"username": username}):
        flash("That username already exists.")
    else:
        users.insert_one({"username": username, "name": f["name"].strip(), "role": f["role"],
                          "password": generate_password_hash(f["password"])})
        flash("Staff member added.")
    return redirect(url_for("staff_list"))


@app.route("/staff/<uid>/delete", methods=["POST"])
@roles("admin")
def staff_delete(uid):
    if uid == session["user"]["id"]:
        flash("You can't delete your own account.")
    else:
        users.delete_one({"_id": oid(uid)})
        flash("Staff member deleted.")
    return redirect(url_for("staff_list"))


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1")
