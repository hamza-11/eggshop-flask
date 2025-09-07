
from app import app, db, User
from getpass import getpass

def change_password():
    """
    A command-line utility to change a user's password.
    """
    username = input("Enter username: ")
    
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        
        if user is None:
            print(f"User '{username}' not found.")
            return
            
        password = getpass("Enter new password: ")
        password_confirm = getpass("Confirm new password: ")
        
        if password != password_confirm:
            print("Passwords do not match.")
            return
            
        user.set_password(password)
        db.session.commit()
        
        print(f"Password for user '{username}' has been updated successfully.")

if __name__ == '__main__':
    change_password()
