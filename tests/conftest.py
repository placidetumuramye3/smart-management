import pytest
from app import create_app
from app.extensions import db

class TestConfig:
    TESTING=True
    SECRET_KEY="test"
    SQLALCHEMY_DATABASE_URI="sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS=False

@pytest.fixture
def app():
    app=create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()
