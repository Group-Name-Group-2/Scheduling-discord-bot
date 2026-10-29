from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from database import db, User

auth_bp = Blueprint('auth', __name__, template_folder='templates')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('events_page'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        # Validation
        if not username or not email or not password:
            flash("All fields are required.", "danger")
            return render_template('register.html')

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template('register.html')

        if User.query.filter((User.username == username) | (User.email == email)).first():
            flash("Username or email already exists.", "warning")
            return render_template('register.html')

        # Create User with Hashed Password
        user = User(username=username, email=email)
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash("Registration successful! Welcome aboard.", "success")
        return redirect(url_for('events_page'))

    return render_template('register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('events_page'))

    if request.method == 'POST':
        login_input = request.form.get('username_or_email', '').strip()
        password = request.form.get('password', '')
        remember = True if request.form.get('remember') else False

        user = User.query.filter(
            (User.username == login_input) | (User.email == login_input.lower())
        ).first()

        if not user or not user.check_password(password):
            flash("Invalid credentials. Please check your username and password.", "danger")
            return render_template('login.html')

        login_user(user, remember=remember)
        next_page = request.args.get('next')
        return redirect(next_page or url_for('events_page'))

    return render_template('login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for('auth.login'))


@auth_bp.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'update_profile':
            steam_id = request.form.get('steam_id', '').strip()
            avatar_url = request.form.get('avatar_url', '').strip()

            current_user.steam_id = steam_id or None
            current_user.avatar_url = avatar_url or None
            db.session.commit()
            flash("Profile updated successfully.", "success")

        elif action == 'change_password':
            old_password = request.form.get('old_password', '')
            new_password = request.form.get('new_password', '')

            if not current_user.check_password(old_password):
                flash("Current password incorrect.", "danger")
            elif len(new_password) < 6:
                flash("New password must be at least 6 characters.", "warning")
            else:
                current_user.set_password(new_password)
                db.session.commit()
                flash("Password updated successfully.", "success")

        return redirect(url_for('auth.account'))

    return render_template('account.html', user=current_user)