import os
import requests
import sqlite3
import datetime
from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_session import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from werkzeug.exceptions import default_exceptions, HTTPException, InternalServerError
from werkzeug.security import check_password_hash, generate_password_hash
from helpers import apology, login_required, lookup, usd

app = Flask(__name__)

# Check for environment variable
if not os.getenv("DATABASE_URL"):
    raise RuntimeError("DATABASE_URL is not set")


# Configure session to use filesystem
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Set up database
engine = create_engine(os.getenv("DATABASE_URL"))
db = scoped_session(sessionmaker(bind=engine))

@app.route("/")
@login_required
def index():
    # Retrieve user's portfolio data
    user_id = session["user_id"]
    rows = db.execute("SELECT symbol, SUM(shares) as total_shares FROM transactions WHERE user_id = ? GROUP BY symbol", (user_id,)).fetchall()

    portfolio = []
    cash = db.execute("SELECT cash FROM users WHERE id = ?", (user_id,)).fetchone()["cash"]

    for row in rows:
        stock_data = lookup(row["symbol"])
        total_value = row["total_shares"] * stock_data["price"]
        portfolio.append({"symbol": row["symbol"],
                          "shares": row["total_shares"],
                          "price": usd(stock_data["price"]),
                          "total": usd(total_value)})

    grand_total = cash + sum([stock["total_value"] for stock in portfolio])

    return render_template("index.html", portfolio=portfolio, cash=usd(cash), grand_total=usd(grand_total))

@app.route("/buy", methods=["GET", "POST"])
@login_required
def buy():
    if request.method == "POST":
        symbol = request.form.get("symbol")
        shares = request.form.get("shares")

        if not symbol:
            return apology("Symbol is required")

        if not shares:
            return apology("Number of shares is required")

        if not shares.isdigit() or int(shares) <= 0:
            return apology("Invalid number of shares")

        quote_data = lookup(symbol)

        if quote_data is None:
            return apology("Invalid symbol")

        total_cost = quote_data["price"] * int(shares)

        cash = db.execute("SELECT cash FROM users WHERE id = ?", (session["user_id"],)).fetchone()["cash"]

        if total_cost > cash:
            return apology("Not enough cash to buy")

        # Insert the purchase transaction into the database
        db.execute("INSERT INTO transactions (user_id, symbol, shares, price) VALUES (?, ?, ?, ?)",
                   (session["user_id"], symbol, int(shares), quote_data["price"]))

        # Update the user's cash balance
        db.execute("UPDATE users SET cash = cash - ? WHERE id = ?", (total_cost, session["user_id"]))

        db.commit()

        return redirect("/")
    else:
        return render_template("buy.html")

@app.route("/history")
@login_required
def history():
    user_id = session["user_id"]
    rows = db.execute("SELECT * FROM transactions WHERE user_id = ?", (user_id,)).fetchall()

    transactions = []

    for row in rows:
        action = "Buy" if row["shares"] > 0 else "Sell"
        stock_data = lookup(row["symbol"])
        transactions.append({"action": action,
                             "symbol": row["symbol"],
                             "shares": abs(row["shares"]),
                             "price": usd(row["price"]),
                             "timestamp": row["timestamp"]})

    return render_template("history.html", transactions=transactions)

@app.route("/login", methods=["GET", "POST"])
def login():
    # Clear any user ID
    session.clear()

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if not username:
            return apology("Username is required")

        if not password:
            return apology("Password is required")

        # Query database for username
        user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

        # Ensure username exists and password is correct
        if user is None or not check_password_hash(user["hash"], password):
            return apology("Invalid username and/or password")

        # Remember which user has logged in
        session["user_id"] = user["id"]

        return redirect("/")
    else:
        return render_template("login.html")

@app.route("/logout")
def logout():
    # Clear session storage
    session.clear()
    return redirect("/")

@app.route("/quote", methods=["GET", "POST"])
@login_required
def quote():
    if request.method == "POST":
        symbol = request.form.get("symbol")

        if not symbol:
            return apology("Symbol is required")

        quote_data = lookup(symbol)

        if quote_data is None:
            return apology("Invalid symbol")

        return render_template("quoted.html", quote=quote_data)
    else:
        return render_template("quote.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    # Clear any user ID
    session.clear()

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        confirmation = request.form.get("confirmation")

        # Ensure username was submitted
        if not username:
            return apology("Username is required")

        # Ensure password was submitted
        if not password:
            return apology("Password is required")

        # Ensure password and confirmation match
        if password != confirmation:
            return apology("Passwords must match")

        # Hash the password
        hashed_password = generate_password_hash(password)

        # Insert the new user into the database
        result = db.execute("INSERT INTO users (username, hash) VALUES (?, ?)", (username, hashed_password))

        if not result:
            return apology("Username already exists")

        # Log in the new user
        user_id = db.execute("SELECT last_insert_rowid() as id").fetchone()["id"]
        session["user_id"] = user_id

        db.commit()

        return redirect("/")
    else:
        return render_template("register.html")

@app.route("/sell", methods=["GET", "POST"])
@login_required
def sell():
    if request.method == "POST":
        symbol = request.form.get("symbol")
        shares = request.form.get("shares")

        if not symbol:
            return apology("Symbol is required")

        if not shares:
            return apology("Number of shares is required")

        if not shares.isdigit() or int(shares) <= 0:
            return apology("Invalid number of shares")

        quote_data = lookup(symbol)

        if quote_data is None:
            return apology("Invalid symbol")

        user_id = session["user_id"]

        # Check if the user owns enough shares to sell
        shares_owned = db.execute("SELECT SUM(shares) as total_shares FROM transactions WHERE user_id = ? AND symbol = ? GROUP BY symbol",
                                  (user_id, symbol)).fetchone()

        if shares_owned is None or shares_owned["total_shares"] < int(shares):
            return apology("Not enough shares to sell")

        total_earnings = quote_data["price"] * int(shares)

        # Insert the sell transaction into the database
        db.execute("INSERT INTO transactions (user_id, symbol, shares, price) VALUES (?, ?, ?, ?)",
                   (user_id, symbol, -int(shares), quote_data["price"]))

        # Update the user's cash balance
        db.execute("UPDATE users SET cash = cash + ? WHERE id = ?", (total_earnings, user_id))

        db.commit()

        return redirect("/")
    else:
        user_id = session["user_id"]
        rows = db.execute("SELECT symbol FROM transactions WHERE user_id = ? GROUP BY symbol HAVING SUM(shares) > 0",
                          (user_id,)).fetchall()

        symbols = [row["symbol"] for row in rows]
        return render_template("sell.html", symbols=symbols)

if __name__ == '__main__':
    app.run(debug=True)