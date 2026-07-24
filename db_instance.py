"""
Single source of truth for the SQLAlchemy db instance and declarative Base.

Imported by app.py, models.py, and all blueprints/routes so that nothing
ever needs to do `from app import db` — which creates a circular import when
any blueprint is registered inside app.py's own module body.
"""
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)
