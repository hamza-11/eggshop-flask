from app import app, db, create_default_users

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        create_default_users()
    app.run(host="0.0.0.0", port=5000)
