import datetime
import os
import json
import numpy
import requests
from prettydate import pretty_date
from flask import Flask, render_template, request
from flask.ext.sqlalchemy import SQLAlchemy
from flask.ext.security import Security, SQLAlchemyUserDatastore, \
    UserMixin, RoleMixin, login_required
from sqlalchemy.orm import class_mapper
from flask_security.core import current_user
from flask_security.forms import RegisterForm, TextField, Required

venmo_url = 'https://api.venmo.com/oauth/authorize?client_id=1351&scope=access_profile,make_payments&response_type=code'

dthandler = lambda obj: pretty_date(obj) if isinstance(obj, datetime.datetime) else None
# Create app
app = Flask(__name__)
app.config['DEBUG'] = True
app.config['SECRET_KEY'] = 'super-secret'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///%s/app.db' % (os.path.abspath(os.path.dirname(__file__)),)
app.config['SECURITY_REGISTERABLE'] = True
app.config['SECURITY_CHANGEABLE'] = True
app.config['SECURITY_SEND_REGISTER_EMAIL'] = False
app.config['VENMO_CLIENT_ID'] = 1454
app.config['VENMO_SECRET'] = "CFELT3xea29gtWFk7ujfTcDh8bMTzJZ8"

COLUMN_BLACKLIST = ["password", "venmo_key"]

# Create database connection object
db = SQLAlchemy(app)

def asdict(obj):
    return dict((col.name, getattr(obj, col.name))
                for col in class_mapper(obj.__class__).mapped_table.c
                    if col.name not in COLUMN_BLACKLIST)

# Define models
roles_users = db.Table('roles_users',
        db.Column('user_id', db.Integer(), db.ForeignKey('user.id')),
        db.Column('role_id', db.Integer(), db.ForeignKey('role.id')))

class SpecialRegisterForm(RegisterForm):
    name = TextField('Full Name')

class Role(db.Model, RoleMixin):
    id = db.Column(db.Integer(), primary_key=True)
    name = db.Column(db.String(80), unique=True)
    description = db.Column(db.String(255))
admin_role = None

friends = db.Table('friends',
        db.Column('f1_id', db.Integer(), db.ForeignKey('user.id')),
        db.Column('f2_id', db.Integer(), db.ForeignKey('user.id')))

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True)
    name = db.Column(db.String(255))
    password = db.Column(db.String(255))
    active = db.Column(db.Boolean())
    created = db.Column(db.DateTime(), default = datetime.datetime.now)
    roles = db.relationship('Role', secondary=roles_users,
                            backref=db.backref('users', lazy='dynamic'))
    friends = db.relationship('User', secondary=friends,
                            primaryjoin=friends.c.f1_id==id, secondaryjoin=friends.c.f2_id==id)
    venmo_key = db.Column(db.String(255))
    venmo_id = db.Column(db.Integer)

transactions_users = db.Table('transactions_users',
        db.Column('user_id', db.Integer(), db.ForeignKey('user.id')),
        db.Column('transaction_id', db.Integer(), db.ForeignKey('transaction.id')))

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=True)
    description = db.Column(db.String(1024), nullable=True)
    vendor = db.Column(db.String(255), nullable=True)
    amount_cents = db.Column(db.Integer, nullable=False)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    creator = db.relationship("User", backref = "created_transactions")
    participants = db.relationship("User", secondary = transactions_users,
                                   backref = "transactions")
    created = db.Column(db.DateTime, default = datetime.datetime.now)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=True)
    event = db.relationship("Event", backref = "transactions")

events_users = db.Table('events_users',
        db.Column('user_id', db.Integer(), db.ForeignKey('user.id')),
        db.Column('event_id', db.Integer(), db.ForeignKey('event.id')))

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255))
    description = db.Column(db.String(1024))
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    creator = db.relationship("User", backref = "created_events")
    participants = db.relationship("User", secondary = events_users,
                                   backref = "events")
    created = db.Column(db.DateTime, default = datetime.datetime.now)
    settled = db.Column(db.Boolean, default=False)
    end_date = db.Column(db.DateTime, default = lambda: datetime.datetime.now()
            + datetime.timedelta(days=2))

payments_users = db.Table('payments_users',
        db.Column('user_id', db.Integer(), db.ForeignKey('user.id')),
        db.Column('payment_id', db.Integer(), db.ForeignKey('payment.id')))

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'))
    amount_cents = db.Column(db.Integer)

# Setup Flask-Security
user_datastore = SQLAlchemyUserDatastore(db, User, Role)
security = Security(app, user_datastore, register_form = SpecialRegisterForm)

@security.context_processor
def security_register_processor():
    return dict(user=current_user)

# Api stuff
@app.route('/api/user/<int:userid>', methods = ['GET'])
def display_user_data(userid):
    return json.dumps(get_user_data(userid), default=dthandler)

def get_user_data(userid):
    user = User.query.filter(User.id==userid).first()
    if not user:
        return 'user not found', 404
    userdict = asdict(user)
    userdict['friends'] = get_friends(userid)
    return userdict

@app.route('/api/user/<int:userid>/friends', methods = ['GET'])
def display_friend_data(userid):
    friends = [get_user_data(friend_id) for friend_id in get_friends(userid)]
    return json.dumps(get_friends(friends), default=dthandler)

@app.route('/api/user/<int:userid>/events', methods = ['GET'])
def get_events_from_userid(userid):
    user = User.query.filter(User.id == userid).first()
    if not user:
        return 'user not found', 404
    return json.dumps([get_event_dict(event.id)
        for event in user.events], default=dthandler)

def get_friends(userid):
    results = db.session.execute(friends.select(friends.c.f1_id == userid)).fetchall()
    return [f2_id for f1_id, f2_id in results]

@app.route('/api/transaction/<int:transid>', methods = ['GET'])
def display_transaction_data(transid):
    transaction_dict = get_transaction_dict(transid)
    return json.dumps(transaction_dict, default=dthandler)

def get_transaction_dict(transid):
    transaction = Transaction.query.filter(Transaction.id == transid).first()
    if not transaction:
        return 'transaction not found', 404
    transaction_dict = asdict(transaction)
    transaction_dict['creator'] = get_user_data(transaction.creator.id)
    transaction_dict['participants'] = [asdict(participant)
            for participant in transaction.participants]
    return transaction_dict

@app.route('/api/event/<int:eventid>', methods = ['GET'])
def display_event_data(eventid):
    event_dict = get_event_dict(eventid)
    return json.dumps(event_dict, default=dthandler)

def get_event_dict(eventid):
    event = Event.query.filter(Event.id == eventid).first()
    if not event:
        return 'event not found', 404
    event_dict = asdict(event)
    event_dict['transactions'] = [get_transaction_dict(transaction.id)
            for transaction in event.transactions]
    event_dict['participants'] = [asdict(participant)
            for participant in event.participants]
    event_dict['creator'] = asdict(event.creator)
    return event_dict

@app.route('/api/transaction', methods = ['POST'])
@login_required
def new_transaction():
    modified_form = request.form.copy()
    participant_ids = [current_user.id]
    if 'participants[]' in modified_form:
        participant_ids += modified_form.getlist('participants[]')
        del modified_form['participants[]']
    modified_form['creator_id'] = current_user.id
    transaction = Transaction(name=modified_form.get('name'),
            description = modified_form.get('description'),
            event_id = modified_form.get('event_id'),
            creator_id = modified_form.get('creator_id'),
            vendor = modified_form.get('vendor'),
            amount_cents = modified_form.get('amount_cents')
            )
    users = User.query.filter(User.id.in_(participant_ids)).all()
    for user in users:
        transaction.participants.append(user)
    db.session.add(transaction)
    db.session.commit()
    return display_transaction_data(transaction.id)

@app.route('/api/event', methods = ['POST'])
@login_required
def new_event():
    modified_form = request.form.copy()
    participant_ids = [current_user.id]
    if 'participants[]' in modified_form:
        participant_ids += modified_form.getlist('participants[]')
        del modified_form['participants[]']
    transaction_ids = []
    if 'transactions[]' in modified_form:
        transaction_ids = modified_form.getlist('transactions[]')
        del modified_form['transactions[]']
    modified_form['creator_id'] = current_user.id
    event = Event(name=modified_form.get('name'),
            description = modified_form.get('description'),
            creator_id = modified_form.get('creator_id')
            )
    db.session.add(event)
    db.session.commit()
    for transaction_id in transaction_ids:
        transaction = Transaction.query.filter(Transaction.id == transaction_id).one()
        transaction.event = event
        db.session.add(transaction)
    users = User.query.filter(User.id.in_(participant_ids)).all()
    for user in users:
        event.participants.append(user)
    db.session.add(event)
    db.session.commit()
    return display_event_data(event.id)

@app.route('/api/transaction/<int:transid>', methods= ['POST'])
@login_required
def edit_transaction(transid):
    trans_to_edit = Transaction.query.filter(Transaction.id == transid).first()
    if not trans_to_edit:
        return 'no such transaction', 404
    if current_user not in trans_to_edit.participants and current_user != trans_to_edit.creator:
        return 'not authorized', 403
    for k,v in request.form.iterlists():
        if len(v) == 1 and not k.endswith('participants'):
            v = v[0]
        if k == 'creator' or k == 'creator_id':
            trans_to_edit.creator_id = v
        elif k == 'event' or k == 'event_id':
            trans_to_edit.event_id = v
        elif k == 'new_participants':
            for participant_id in v:
                trans_to_edit.participants.append(
                    User.query.filter(User.id == participant_id).one())
        elif k == 'all_participants' or k == 'participants':
            del trans_to_edit.participants[:]
            for participant_id in v:
                trans_to_edit.participants.append(User.query.filter(User.id == participant_id).one())
        elif k == 'del_participants':
            db.session.execute(transactions_users.delete().where(
                transactions_users.c.user_id.in_(v)).where(
                transactions_users.c.transaction_id == trans_to_edit.id))
        else:
            setattr(trans_to_edit, k, v)
    db.session.add(trans_to_edit)
    db.session.commit()
    return display_transaction_data(trans_to_edit.id)

@app.route('/api/event/<int:eventid>', methods=['POST'])
@login_required
def edit_event(eventid):
    event_to_edit = Event.query.filter(Event.id == eventid).first()
    if not event_to_edit:
        return 'no such event', 404
    if current_user not in event_to_edit.participants:
        return 'not authorized', 403
    for k,v in request.form.iterlists():
        if len(v) == 1 and not k.endswith('participants') and not k.endswith('transactions'):
            v = v[0]
        if k == 'creator' or k == 'creator_id':
            event_to_edit.creator_id = v
        elif k == 'new_participants':
            for participant_id in v:
                event_to_edit.participants.append(
                    User.query.filter(User.id == participant_id).one())
        elif k == 'all_participants' or k == 'participants':
            del event_to_edit.participants[:]
            for participant_id in v:
                event_to_edit.participants.append(User.query.filter(User.id == participant_id).one())
        elif k == 'del_participants':
            db.session.execute(events_users.delete().where(
                events_users.c.user_id.in_(v)).where(
                events_users.c.event_id == event_to_edit.id))
        elif k == 'new_transactions':
            for trans_id in v:
                event_to_edit.transactions.append(
                    Transaction.query.filter(Transaction.id == trans_id).one())
        elif k == 'all_transactions' or k == 'transactions':
            del event_to_edit.transactions[:]
            for trans_id in v:
                event_to_edit.transactions.append(
                    Transaction.query.filter(Transaction.id == trans_id).one())
        elif k == 'del_transactions':
            for trans_id in v:
                edit_trans = Transaction.query.filter(Transaction.id == trans_id).one()
                edit_trans.event = None
                db.session.add(edit_trans)
        else:
            setattr(event_to_edit, k, v)
    db.session.add(event_to_edit)
    db.session.commit()
    return display_event_data(event_to_edit.id)

@app.route('/api/event/<int:eventid>/adduser', methods = ['POST'])
@login_required
def add_user(eventid):
    user_email = request.form.get('email')
    user = User.query.filter(User.email == user_email).first()
    if not user:
        return json.dumps({'error' : True})
    event = Event.query.filter(Event.id == eventid).first()
    if not event:
        return json.dumps({'error' : True, 'message' : 'jesus christ what did you doooooooo'})
    if not user in event.participants:
        event.participants.append(user)
        db.session.add(event)
        db.session.commit()
    event_dict = get_event_dict(eventid)
    event_dict['error'] = False
    return json.dumps(event_dict, default=dthandler)

@app.route('/api/user/<int:userid>', methods = ['POST'])
@login_required
def edit_user(userid):
    if userid != current_user.id and admin_role not in current_user.roles:
        return 'NOT AUTHORIZED', 403
    user_to_edit = User.query.filter(User.id == userid).first()
    if not user_to_edit:
        return 'no such user', 404
    for k,v in request.form.items():
        if (len(v) == 1 and not k.endswith('friends')
                and not k.endswith('events') and not k.endswith('transactions')):
            v = v[0]
        if k == 'password' or k == 'roles':
            return 'dumbass.', 403
        elif k == 'friends' or k == 'transactions' or k == 'events':
            return 'prefix that with new_ or all_ for append or replace', 422
        elif k == 'new_friends':
            for new_friend_id in v:
                new_friend = User.query.filter(User.id == new_friend_id).one()
                user_to_edit.friends.append(new_friend)
        elif k == 'all_friends':
            del user_to_edit.friends[:]
            for new_friend_id in v:
                new_friend = User.query.filter(User.id == new_friend_id).one()
                user_to_edit.friends.append(new_friend)
        elif k == 'del_friends':
            for old_friend_id in v:
                old_friend = User.query.filter(User.id == old_friend_id).one()
                user_to_edit.friends.remove(old_friend)
        elif k == 'new_transactions':
            for trans_id in v:
                user_to_edit.transactions.append(Transaction.query.filter(
                    Transaction.id == trans_id).one())
        elif k == 'all_transactions':
            del user_to_edit.transactions[:]
            for trans_id in v:
                user_to_edit.transactions.append(Transaction.query.filter(
                    Transaction.id == trans_id).one())
        elif k == 'del_transactions':
            db.session.execute(transactions_users.delete().where(
                transactions_users.c.transaction_id.in_(v)).where(
                transactions_users.c.user_id == user_to_edit.id))
        elif k == 'new_events':
            for event_id in v:
                new_event = Event.query.filter(Event.id == event_id).one()
                user_to_edit.events.append(new_event)
        elif k == 'all_events':
            del user_to_edit.events[:]
            for event_id in v:
                new_event = Event.query.filter(Event.id == event_id).one()
                user_to_edit.events.append(new_event)
        elif k == 'del_events':
            db.session.execute(events_users.delete().where(
                events_users.c.event_id.in_(v)).where(
                events_users.c.user_id == user_to_edit.id))
        else:
            setattr(user_to_edit, k, v)
    db.session.add(user_to_edit)
    db.session.commit()
    return display_user_data(user_to_edit.id)

#Venmo
@app.route('/api/venmo/accesstoken', methods = ['GET'])
@login_required
def get_access_oken():
    AUTHORIZATION_CODE = request.args.get('code')
    data = {
        "client_id":app.config['VENMO_CLIENT_ID'],
        "client_secret":app.config['VENMO_SECRET'],
        "code":AUTHORIZATION_CODE
        }
    url = "https://api.venmo.com/oauth/access_token"
    response = requests.post(url, data)
    response_dict = response.json()
    access_token = response_dict.get('access_token')
    user = User.query.filter(User.id == current_user.id).one()
    user.venmo_key = access_token
    user.venmo_id = user_id
    db.session.add(user)
    db.commit()
    return redirect(url_for('account'))

def schedule_transaction(recipient_id, amount, user_id):
    user = User.query.filter(User.id == user_id).one()
    access_token = user.venmo_key
    user_venmo_id = user.venmo_id
    values = {'access_token':access_token, 'user_id':user_venmo_id, "amount":amount, "note":"Paying with Reimburst!"}
    r = requests.post("https://api.venmo.com/v1/payments", params=values)
    transaction_result = json.loads(res)

def venmo_error_check(request):
    if request.status_code not requests.codes.ok:
        #um
        #panic

def addPayment(r_id, s_id, amt, e_id):
    pay = Payment(recipient_id = r_id, sender_id = s_id, amount_cents = amt, event_id = e_id)
    db.session.add(pay)
    db.session.commit()

@app.route('/api/venmo/settle', methods = ['POST'])
def settle():
    eventid = request.form.get('eventid')
    event = Events.query.filter(Events.id == eventid).one()
    if event.settled:
        return "Event already settled.", 400
    users_to_payments = {}
    users_to_owed_amt = {}
    for transaction in event.transactions:
        if transaction.creator_id not in users_to_payments:
            users_to_payments[transaction.creator.id] = 0
        users_to_payments[transaction.creator.id] += transaction.amount_cents
        for participant_id in [x.id for x in transaction.participants]:
            if participant_id not in users_to_owed_amt:
                users_to_owed_amt[participant_id] = 0
            users_to_owed_amt[participant_id] = transaction.amount_cents / len(transaction.participants)
    users_to_total_diff = {}
    for user, amt in users_to_payments.items():
        users_to_total_diff[user] = amt
    for user, amt in users_to_owed_amt.items():
        users_to_total_diff[user] = user_to_total_diff.get(user, 0) - amt
    for user in users_to_total_diff:
        if users_to_total_diff[user] > 0:
            to_resolve = users_to_total_diff[user]
            for otheruser, amt in users_to_total_diff.items():
                if amt < 0:
                    if -amt >= to_resolve:
                        schedule_transaction(otheruser, user, to_resolve)
                        to_resolve = 0
                        users_to_total_diff[otheruser] += to_resolve
                        users_to_total_diff[user] -= to_resolve
                        break
                    else:
                        schedule_transaction(otheruser, user, amt)
                        to_resolve -= amt
                        users_to_total_diff[otheruser] += amt
                        users_to_total_diff[user] -= amt
    event.settled = True
    db.session.add(event)
    db.session.commit()
    return 'BAM!  RESOLVED!'

@app.route('/api/user/<int:user_id>/event/<int:event_id>/paymentsdue', methods = ['GET'])
@login_required
def get_payments(user_id, event_id):
    payments = Payments.query.filter(Payments.sender_id == user_id).filter(Payments.event_id == event_id).all()
    user_payments = []
    for payment in payments:
        payment_dict = {}
        payment_dict['recipient_id'] = payment.recipient_id
        payment_dict['amount_cents'] = payment.amount_cents
        payment_dict['event_id'] = event_id
        user_payments.append(payment_dict)
    return json.dumps(user_payments, default=dthandler)

# Views
@app.route('/')
def home():
    return render_template('home.html', user=current_user)

@app.route('/account')
@login_required
def account():
    return render_template('account.html', user=current_user)

@app.route('/event/<id>')
@login_required
def event(id):
    event_obj = Event.query.filter(Event.id == id).first()
    if not event_obj:
        return 'not found', 404
    return render_template('event.html', user=current_user, eventID=id, event=event_obj)

if __name__ == '__main__':
    admin_role = Role.query.filter(Role.name == 'admin').first()
    if not admin_role:
        admin_role = Role(name='admin', description='admin user')
        db.session.add(admin_role)
        db.session.commit()
    app.run()
