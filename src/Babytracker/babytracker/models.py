import hashlib
from datetime import timedelta

from sqlalchemy import Column, ForeignKey, desc
from sqlalchemy import  String, Enum, Integer, Date, DateTime, Interval
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.orm import relationship, backref
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.ext.declarative import declarative_base

from zope.interface import implements
from zope.sqlalchemy import ZopeTransactionExtension

from pyramid.security import Everyone, Authenticated, Allow, Deny, DENY_ALL

from babytracker.interfaces import IJSONCapable
from babytracker.interfaces import VIEW_PERMISSION, EDIT_PERMISSION, SIGNUP_PERMISSION

DBSession = scoped_session(sessionmaker(extension=ZopeTransactionExtension()))
Base = declarative_base()


class Root(object):
    """Root factory
    """

    _instance = None

    # Singleton factory - this class has no state anyway, but want to be
    # able to record lineage to it via ``__parent__``
    def __new__(cls, request=None):
        if cls._instance is None:
            cls._instance = object.__new__(cls)
        return cls._instance

    # Traversal

    __name__ = None
    __parent__ = None

    def __getitem__(self, name):
        if '@' not in name or name.startswith('@'): # view or not an email address
            raise KeyError(name)

        session = DBSession()
        try:
            return session.query(User).filter_by(email=name).one()
        except NoResultFound:
            raise KeyError(name)

    # Security

    __acl__ = [
            (Deny, Authenticated, SIGNUP_PERMISSION),
            (Allow, Everyone, (VIEW_PERMISSION, SIGNUP_PERMISSION,)),
            DENY_ALL,
        ]

class User(Base):
    implements(IJSONCapable)
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True)
    name = Column(String)
    password = Column(String)

    _parent = None # transient

    def __init__(self, email, name, password):
        self.email = email
        self.name = name
        self.password = self._hash_password(password)

    # Operations

    @classmethod
    def _hash_password(self, password):
        return hashlib.sha1(password).hexdigest()

    @classmethod
    def authenticate(cls, email, password):
        """Attempt to find and return a ``User`` object with the given username
        and password. The password should be in plain text as entered by
        the user. Returns ``None`` if no user could be found.
        """
        session = DBSession()
        try:
            return session.query(User).filter_by(
                email=email,
                password=cls._hash_password(password)
            ).one()
        except NoResultFound:
            return None

    def change_password(self, new_password):
        """Set a new password. ``new_password`` should be in plain text. The
        password will be stored hashed.
        """
        self.password = self._hash_password(new_password)

    # Traversal

    @property
    def __name__(self):
        return self.email

    @property
    def __parent__(self):
        return Root()

    def __getitem__(self, name):
        for baby in self.babies:
            if baby.__name__ == name:
                return baby
        raise KeyError(name)

    # Security

    @property
    def __acl__(self):
        return [
            (Allow, self.__name__, (VIEW_PERMISSION, EDIT_PERMISSION,)),
            DENY_ALL,
        ]

    # JSON representation

    def to_json_dict(self):
        return {
            'email': self.email,
            'name': self.name,
        }

class Baby(Base):
    implements(IJSONCapable)
    __tablename__ = 'babies'

    id = Column(Integer, primary_key=True)
    dob = Column(Date)
    name = Column(String)
    gender = Column(Enum('m', 'f', name='genders'))

    user_id = Column(Integer, ForeignKey('users.id'))
    user = relationship("User", backref=backref('babies', order_by=id))

    def __init__(self, user, dob, name, gender):
        self.user = user
        self.dob = dob
        self.name = name
        self.gender = gender
        self.entries = []

    # Operations

    def get_entries_between(self, start, end, entry_type=None):
        """Return a list of entries in reverse order between the start and end
        datetimes, inclusive
        """
        session = DBSession()

        cls = Entry
        if entry_type is not None:
            cls = entry_type

        query = session.query(cls).filter(cls.baby==self)

        if start is not None:
            query = query.filter(cls.start>=start)
        if end is not None:
            query = query.filter(cls.start<=end)

        return query.order_by(desc(cls.start))

    @staticmethod
    def normalize_name(name):
        return name.strip().lower().replace(' ', '-')

    # Traversal

    @property
    def __name__(self):
        return self.normalize_name(self.name)

    @property
    def __parent__(self):
        return self.user

    def __getitem__(self, name):
        entry_id = None
        try:
            entry_id = int(name)
        except:
            raise KeyError(name)

        session = DBSession()
        try:
            return session.query(Entry).filter_by(id=entry_id).one()
        except NoResultFound:
            raise KeyError(name)

    # JSON representation

    def to_json_dict(self):
        return {
            'dob': self.dob.isoformat(),
            'name': self.name,
            'gender': self.gender,
        }

# Registry of entry types - used as class decorator
_entry_types = {}
def entry_type(cls):
    """Class decorator to register an entry type based on the mapper arg
    polymorphic_identity.
    """
    name = cls.__mapper_args__['polymorphic_identity']
    _entry_types[name] = cls
    return cls

def lookup_entry_type(name, default=None):
    return _entry_types.get(name, default)

class Entry(Base):
    implements(IJSONCapable)
    __tablename__ = 'entries'

    id = Column(Integer, primary_key=True)
    type = Column(String)

    start = Column(DateTime)
    end = Column(DateTime, nullable=True)
    note = Column(String, nullable=True)

    baby_id = Column(Integer, ForeignKey('babies.id'))
    baby = relationship("Baby", backref=backref('entries', order_by=start.desc))

    __mapper_args__ = {'polymorphic_on': type}

    def __init__(self, baby, start, end=None, note=None):
        self.baby = baby
        self.start = start
        self.end = end
        self.note = note

    # Traversal

    @property
    def __name__(self):
        return unicode(self.id)

    @property
    def __parent__(self):
        return self.baby

    def __getitem__(self, name):
        raise KeyError(name)

    # JSON representation

    def to_json_dict(self):
        return {
            'entry_type': self.type,
            'start': self.start.isoformat() if self.start is not None else None,
            'end': self.end.isoformat() if self.end is not None else None,
            'note': self.note,
        }

@entry_type
class BreastFeed(Entry):
    __mapper_args__ = {'polymorphic_identity': 'breast_feed'}

    left_duration = Column(Interval)
    right_duration = Column(Interval)

    def __init__(self, baby, start, left_duration=timedelta(0), right_duration=timedelta(0), end=None, note=None):
        super(BreastFeed, self).__init__(baby, start, end, note)
        self.left_duration = left_duration
        self.right_duration = right_duration

    # JSON representation

    def to_json_dict(self):
        entry = super(BreastFeed, self).to_json_dict()
        entry.update({
            'left_duration': self.left_duration.seconds / 60,
            'right_duration': self.right_duration.seconds / 60,
        })
        return entry

@entry_type
class BottleFeed(Entry):
    __mapper_args__ = {'polymorphic_identity': 'bottle_feed'}

    amount = Column(Integer)

    def __init__(self, baby, start, amount, end=None, note=None):
        super(BottleFeed, self).__init__(baby, start, end, note)
        self.amount = amount

    # JSON representation

    def to_json_dict(self):
        entry = super(BottleFeed, self).to_json_dict()
        entry.update({
            'amount': self.amount,
        })
        return entry

@entry_type
class MixedFeed(BreastFeed):
    __mapper_args__ = {'polymorphic_identity': 'mixed_feed'}

    topup = Column(Integer)

    def __init__(self, baby, start, left_duration=timedelta(0), right_duration=timedelta(0), topup=0, end=None, note=None):
        self.baby = baby
        self.start = start
        self.left_duration = left_duration
        self.right_duration = right_duration
        self.topup = topup
        self.end = end
        self.note = note

    # JSON representation

    def to_json_dict(self):
        entry = super(MixedFeed, self).to_json_dict()
        entry.update({
            'topup': self.topup,
        })
        return entry

@entry_type
class Sleep(Entry):
    __mapper_args__ = {'polymorphic_identity': 'sleep'}

    duration = Column(Interval)

    def __init__(self, baby, start, duration, end=None, note=None):
        super(Sleep, self).__init__(baby, start, end, note)
        self.duration = duration

    # JSON representation

    def to_json_dict(self):
        entry = super(Sleep, self).to_json_dict()
        entry.update({
            'duration': self.duration.seconds / 60,
        })
        return entry

@entry_type
class NappyChange(Entry):
    __mapper_args__ = {'polymorphic_identity': 'nappy_change'}

    contents = Column(Enum('wet', 'dirty', 'none', name='nappy_states'))

    def __init__(self, baby, start, contents, end=None, note=None):
        super(NappyChange, self).__init__(baby, start, end, note)
        self.contents = contents

    # JSON representation

    def to_json_dict(self):
        entry = super(NappyChange, self).to_json_dict()
        entry.update({
            'contents': self.contents,
        })
        return entry

# TODO: Other types of entries:
# - solid foods
# - play
# - development milestone
