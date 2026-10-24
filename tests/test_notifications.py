from app.extensions import db
from app.models import User, UserNotification


def make_user(username):
    user = User(username=username, full_name=username.title(), role="student")
    user.set_password("test-password")
    db.session.add(user)
    db.session.flush()
    return user


def make_notification(user, title):
    notification = UserNotification(
        user_id=user.id,
        category="Test",
        title=title,
        body="Test notification body",
        link="/notifications",
        created_at="2026-10-24 09:00:00",
    )
    db.session.add(notification)
    db.session.flush()
    return notification


def signed_in_client(app, user):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True
    return client


def test_user_can_delete_one_of_their_notifications(app):
    with app.app_context():
        owner = make_user("notification_owner")
        other_user = make_user("notification_other")
        selected = make_notification(owner, "Selected")
        unrelated = make_notification(owner, "Keep this")
        other_user_notification = make_notification(other_user, "Private to other user")
        owner_id = owner.id
        selected_id = selected.id
        unrelated_id = unrelated.id
        other_notification_id = other_user_notification.id
        db.session.commit()

    response = signed_in_client(app, owner).post("/notifications", data={
        "action": "delete_notification",
        "notification_id": str(selected_id),
    })

    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(UserNotification, selected_id) is None
        assert db.session.get(UserNotification, unrelated_id).user_id == owner_id
        assert db.session.get(UserNotification, other_notification_id) is not None


def test_user_cannot_delete_another_users_notification(app):
    with app.app_context():
        owner = make_user("notification_owner")
        other_user = make_user("notification_other")
        protected = make_notification(other_user, "Protected")
        protected_id = protected.id
        owner_id = owner.id
        db.session.commit()

    response = signed_in_client(app, owner).post("/notifications", data={
        "action": "delete_notification",
        "notification_id": str(protected_id),
    })

    assert response.status_code == 404
    with app.app_context():
        assert db.session.get(UserNotification, protected_id).user_id != owner_id


def test_delete_all_removes_only_the_signed_in_users_notifications(app):
    with app.app_context():
        owner = make_user("notification_owner")
        other_user = make_user("notification_other")
        make_notification(owner, "Owner one")
        make_notification(owner, "Owner two")
        protected = make_notification(other_user, "Other user notification")
        protected_id = protected.id
        owner_id = owner.id
        db.session.commit()

    response = signed_in_client(app, owner).post("/notifications", data={"action": "delete_all"})

    assert response.status_code == 302
    with app.app_context():
        assert UserNotification.query.filter_by(user_id=owner_id).count() == 0
        assert db.session.get(UserNotification, protected_id) is not None
