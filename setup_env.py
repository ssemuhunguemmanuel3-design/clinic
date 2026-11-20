"""Asks for your MongoDB Atlas connection string, tests it, and writes .env"""
import secrets
from pymongo import MongoClient

print("\nPaste your MongoDB Atlas connection string.")
print("It looks like: mongodb+srv://USER:PASSWORD@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority")
print("(Make sure you replaced <password> with your real password.)\n")

while True:
    uri = input("Connection string: ").strip().strip('"')
    if "<password>" in uri or not uri.startswith("mongodb"):
        print("That doesn't look right. Replace <password> with your database user's password.\n")
        continue
    print("Testing connection...")
    try:
        MongoClient(uri, serverSelectionTimeoutMS=8000).admin.command("ping")
        print("Connected to MongoDB Atlas!\n")
        break
    except Exception as e:
        print("Could not connect:", str(e)[:300])
        print("Check: password correct (special characters must be URL-encoded, @ = %40),")
        print("and Network Access in Atlas allows your IP (or 0.0.0.0/0).\n")

admin_pw = input("Choose an admin password (press Enter for admin123): ").strip() or "admin123"
clinic = input("Clinic name (press Enter for City Health Clinic): ").strip() or "City Health Clinic"

with open(".env", "w", encoding="utf-8") as f:
    f.write(f"MONGO_URI={uri}\nDB_NAME=clinic\nSECRET_KEY={secrets.token_hex(32)}\n"
            f"ADMIN_PASSWORD={admin_pw}\nCLINIC_NAME={clinic}\n")
print("Saved .env. Starting the app...\n")
