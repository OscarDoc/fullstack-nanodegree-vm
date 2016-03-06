import os
import random
import string
import httplib2
import json
import requests
from datetime import datetime
from daos import UserDAO, RestaurantDAO, MenuItemDAO
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session as login_session, make_response
from werkzeug import SharedDataMiddleware, secure_filename
from werkzeug.contrib.atom import AtomFeed
from oauth2client.client import flow_from_clientsecrets, FlowExchangeError, OAuth2Credentials


UPLOAD_FOLDER = 'uploads'
ALLOWED_FILES = set(['png', 'jpg', 'jpeg', 'gif'])
CLIENT_ID = json.loads(open('client_secrets_gc.json', 'r').read())['web']['client_id']

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 # 1 Megabyte
app.add_url_rule('/uploads/<filename>', 'uploaded_file', build_only=True)
app.wsgi_app = SharedDataMiddleware(app.wsgi_app, {
    '/uploads':  app.config['UPLOAD_FOLDER']
})

usr_dao = UserDAO()
rst_dao = RestaurantDAO()
mnu_dao = MenuItemDAO()


# JSON Endpoints 

@app.route('/restaurants/JSON')
def restaurants_json():
	restaurants = rst_dao.get_all_restaurants()
	return jsonify(Restaurants=[r.serialize for r in restaurants])


@app.route('/restaurants/<int:restaurant_id>/menu/JSON')
def restaurant_menu_json(restaurant_id):
	items = mnu_dao.get_menu_by_restaurant(restaurant_id)
	return jsonify(MenuItems=[i.serialize for i in items])


@app.route('/restaurants/<int:restaurant_id>/menu/<int:menu_id>/JSON')
def menu_item_json(restaurant_id, menu_id):
	item = mnu_dao.get_menu(menu_id)
	return jsonify(MenuItem=item.serialize)


# Atom Endpoints

@app.route('/restaurants/ATOM')
def restaurants_atom():
	feed = AtomFeed('Restaurants', feed_url=request.url, url=url_for('show_restaurants'))

	restaurants = rst_dao.get_all_restaurants()
	for r in restaurants:
		feed.add(r.name, unicode(r.name),
                 content_type='html',
                 id=r.id,
                 url=url_for('show_menu', restaurant_id=r.id),
                 updated=datetime.today())
	return feed.get_response()


@app.route('/restaurants/<int:restaurant_id>/menu/ATOM')
def restaurant_menu_atom(restaurant_id):
	restaurant = rst_dao.get_restaurant(restaurant_id)
	feed = AtomFeed('%s menu' %restaurant.name,
		feed_url=request.url,
		url=url_for('show_menu', restaurant_id = restaurant_id))

	items = mnu_dao.get_menu_by_restaurant(restaurant_id)
	for i in items:
		feed.add(i.name, unicode(i.description),
                 content_type='html',
                 id=i.id,
                 url=url_for('show_menu', restaurant_id=restaurant_id),
                 updated=datetime.today())
	return feed.get_response()


@app.route('/restaurants/<int:restaurant_id>/menu/<int:menu_id>/ATOM')
def menu_item_atom(restaurant_id, menu_id):
	restaurant = rst_dao.get_restaurant(restaurant_id)
	item = mnu_dao.get_menu(menu_id)

	feed = AtomFeed(item.name,
		feed_url=request.url,
		url=url_for('show_menu', restaurant_id = restaurant_id))

	feed.add(item.name, unicode(item.description),
             content_type='html',
             id=item.id,
             url=url_for('show_menu', restaurant_id=restaurant_id),
             updated=datetime.today())
	return feed.get_response()


# Helper functions

def json_response(http_code, text):
	response = make_response(json.dumps(text), http_code)
	response.headers['Content-type'] = 'application/json'
	return response


def is_logged():
	return 'username' in login_session


def is_owners_session(obj):
	return is_logged() and obj.user_id == login_session['user_id']


def allowed_file(filename):
	return '.' in filename and \
		filename.rsplit('.', 1)[1] in ALLOWED_FILES



# Web routes

# Login route
@app.route('/login')
def showLogin():
	state = ''.join(random.choice(string.ascii_uppercase + string.digits) 
		for x in xrange(32))
	login_session['state'] = state
	return render_template('login.html', STATE=state)


# Login with Google Connect
@app.route('/gconnect', methods=['POST'])
def gconnect():
	if request.args.get('state') != login_session['state']:
		return json_response(401, 'Invalid state paremeter.')
	code = request.data
	try:
		ouath_flow = flow_from_clientsecrets('client_secrets.json', scope='')
		ouath_flow.redirect_uri = 'postmessage'
		credentials = ouath_flow.step2_exchange(code)
	except FlowExchangeError:
		return json_response(401, 'Failed to upgrade the authorization code.')

	access_token = credentials.access_token
	url = ('https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=%s'
		% access_token)
	h = httplib2.Http()
	result = json.loads(h.request(url, 'GET')[1])

	# If there is an error, abort
	if result.get('error') is not None:
		return json_response(500, result.get('error'))

	# Verify that token is used for the indended user
	gplus_id = credentials.id_token['sub']
	if result['user_id'] != gplus_id:
		return json_response(401, 'Token and given user IDs don\'t match.')

	# Verify that token is valid for this app
	if result['issued_to'] != CLIENT_ID:
		return json_response(401, 'Token and app client IDs don\'t match.')

	# Check if user is already logged in
	stored_credentials = login_session.get('credentials')
	stored_gplus_id = login_session.get('gplus_id')
	if stored_credentials is not None\
			and gplus_id == stored_gplus_id:
		response = json_response(200, 'Current user already connected')

	# Store access token in the session for later use
	login_session['credentials'] = credentials.to_json()
	login_session['gplus_id'] = gplus_id

	# Get user info
	userinfo_url = 'https://www.googleapis.com/oauth2/v1/userinfo'
	params = {'access_token': credentials.access_token, 'alt': 'json'}
	answer = requests.get(userinfo_url, params=params)
	data = json.loads(answer.text)

	login_session['username'] = data['name']
	login_session['picture'] = data['picture']
	login_session['email'] = data['email']

	# See if user exists, if not make a new one
	user_id = usr_dao.get_user_id(data['email'])
	if not user_id:
		user_id = usr_dao.add_user(
			data['name'], 
			data['email'], 
			data['picture'])
	login_session['user_id'] = user_id

	output = ''
	output += '<h1>Welcome, '
	output += login_session['username']
	output += '!</h1>'
	output += '<img src="'
	output += login_session['picture']
	output += '" style="width: 300px; height: 300px;border-radius: 150px;-webkit-border-radius: 150px;-moz-border-radius: 150px;">'
	flash('You are now logged in as %s' % login_session['username'], 'success')
	return output


# Disconnect Google Connect, revoking user's token and ressetting its session
@app.route('/gdisconnect')
def gdisconnect():
	# Abort if not connected
	credentials = login_session.get('credentials')
	if credentials is None:
		return json_response(401, 'Current user not connected')

	credentials = OAuth2Credentials.from_json(credentials)
	# Execute HTTP GET to revoke current token
	access_token = credentials.access_token
	url = 'https://accounts.google.com/o/oauth2/revoke?token=%s' % access_token
	print url
	h = httplib2.Http()
	result = h.request(url, 'GET')[0]

	if result['status'] == '200':
		# Reset session
		del login_session['credentials']
		del login_session['gplus_id']
		del login_session['username']
		del login_session['email']
		del login_session['picture']
		flash('Successfully disconnected', 'success')
		return redirect(url_for('show_restaurants'))
	else:
		flash("Failed to revoke user's token", 'error')
		return redirect(url_for('show_restaurants'))


# Our index, shows a list of all the restaurants, plus restaurant create/edit/delete links
@app.route('/')
@app.route('/restaurants')
def show_restaurants():
	restaurants = rst_dao.get_all_restaurants()

	if 'username' not in login_session:
		return render_template(
			'publicrestaurants.html',
			restaurants = restaurants)
	else:
		return render_template(
			'restaurants.html',
			restaurants = restaurants,
			username = login_session['username'])


# Form to create a new restaurant
@app.route('/restaurants/new',
	methods=['GET', 'POST'])
def new_restaurant():
	if not is_logged():
		return redirect('login')

	if request.method == 'GET':
		return render_template('newrestaurant.html',
			username = login_session['username'])

	# Else: it's a POST
	new_name = request.form['name'].strip()
	rst_dao.add_restaurant(new_name, login_session['user_id'])
	flash('Restaurant %s succesfully added' % new_name, 'success')
	return redirect(url_for('show_restaurants'))


# Form to edit an existing restaurant
@app.route('/restaurants/<int:restaurant_id>/edit',
	methods=['GET', 'POST'])
def edit_restaurant(restaurant_id):
	if not is_logged():
		return redirect('login')

	restaurant = rst_dao.get_restaurant(restaurant_id)
	if not is_owners_session(restaurant):
		return "<script>function f(){alert('You are not authorized to edit this restaurant.');};</script><body onload='f()'></body>"

	if request.method == 'GET':
		return render_template('editrestaurant.html', restaurant = restaurant)

	# Else: it's a POST
	new_name = request.form['name'].strip()
	rst_dao.set_restaurant_name(restaurant_id, new_name)
	flash('Restaurant name succesfully changed to %s' %new_name, 'success')
	return redirect(url_for('show_restaurants'))


# Form to delete a restaurant
@app.route('/restaurants/<int:restaurant_id>/delete',
	methods=['GET', 'POST'])
def delete_restaurant(restaurant_id):
	if not is_logged():
		return redirect('login')

	restaurant = rst_dao.get_restaurant(restaurant_id)
	if not is_owners_session(restaurant):
		return "<script>function f(){alert('You are not authorized to delete this restaurant.');};</script><body onload='f()'></body>"

	if request.method == 'GET':		
		return render_template(
			'deleterestaurant.html', 
			restaurant = restaurant, 
			username = login_session['username'])

	# Else: it's a POST
	rst_dao.delete_restaurant(restaurant_id)
	flash('Restaurant %s deleted' %restaurant.name, 'success')
	return redirect(url_for('show_restaurants'))


# List of the menu items in a restaurant, plus item create/edit/delete links
@app.route('/restaurants/<int:restaurant_id>/')
@app.route('/restaurants/<int:restaurant_id>/menu')
def show_menu(restaurant_id):
	restaurant = rst_dao.get_restaurant(restaurant_id)
	items = mnu_dao.get_menu_by_restaurant(restaurant_id)
	creator = usr_dao.get_user(restaurant.user_id)

	# If logged user is the creator, show owner's page
	if is_owners_session(restaurant):
		return render_template(
			'menu.html', 
			restaurant = restaurant, 
			items = items,
			creator = creator,
			username = login_session['username'])
	# Else, show public page
	else:
		return render_template(
			'publicmenu.html', 
			restaurant = restaurant, 
			items = items,
			creator = creator)


# Form to create a new menu item in the restaurant
@app.route('/restaurants/<int:restaurant_id>/new/',
	methods=['GET', 'POST'])
def new_menu_item(restaurant_id):
	if not is_logged():
		return redirect('login')

	restaurant = rst_dao.get_restaurant(restaurant_id)
	if not is_owners_session(restaurant):
		return "<script>function f(){alert('You are not authorized to add a menu item.');};</script><body onload='f()'></body>"

	if request.method == 'GET':
		return render_template(
			'newmenuitem.html', 
			restaurant_id = restaurant_id, 
			restaurant_name=restaurant.name,
			username = login_session['username'])
	
	# Else it's a POST
	new_name = request.form['name'].strip()
	mnu_dao.add_menu_item(restaurant_id, new_name)
	flash('New menu item created', 'success')

	return redirect(url_for('show_menu', restaurant_id = restaurant_id))



# Form to edit an existing menu item
@app.route('/restaurants/<int:restaurant_id>/<int:menu_id>/edit/',
	methods=['GET', 'POST'])
def edit_menu_item(restaurant_id, menu_id):
	if not is_logged():
		return redirect('login')

	item = mnu_dao.get_menu(menu_id)
	if not is_owners_session(item):
		return "<script>function f(){alert('You are not authorized to edit this menu item.');};</script><body onload='f()'></body>"

	if request.method == 'GET':
		restaurant = rst_dao.get_restaurant(restaurant_id)
		return render_template('editmenuitem.html',
								restaurant = restaurant,
								item = item,
								username = login_session['username'])

	# Else it's a POST
	new_name = request.form['name'].strip()
	cur_name = mnu_dao.get_menu_name(menu_id)
	if new_name != '' and new_name != cur_name:
		mnu_dao.set_menu_name(menu_id, new_name)
		flash('Menu item name succesfully changed to %s' %new_name, 'success')

	new_description = request.form['description'].strip()
	cur_description = mnu_dao.get_menu_description(menu_id)
	if new_description != '' and new_description != cur_description:
		mnu_dao.set_menu_description(menu_id, new_description)
		flash('Menu item description succesfully changed to %s' %new_description, 'success')

	new_price = request.form['price'].strip()
	cur_price = mnu_dao.get_menu_price(menu_id)
	if new_price != '' and new_price != cur_price:
		mnu_dao.set_menu_price(menu_id, new_price)
		flash('Menu item price succesfully changed to %s' %new_price, 'success')

	new_course = request.form['course']
	cur_course = mnu_dao.get_menu_course(menu_id)
	if new_course != '' and new_course != cur_course:
		mnu_dao.set_menu_course(menu_id, new_course)
		flash('Menu item course succesfully changed to %s' %new_course, 'success')

	new_image = request.files['image']
	if new_image and allowed_file(new_image.filename):
		# Prepend the menu_id to the name to make it unique
		filename = `menu_id` + secure_filename(new_image.filename)
		new_image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
		mnu_dao.set_menu_image(menu_id, filename)
		flash('Menu item image succesfully changed', 'success')
		#return redirect(url_for('uploaded_file', filename=filename))

	return redirect(url_for('show_menu', restaurant_id = restaurant_id))



# Form to delete an existing menu item
@app.route('/restaurants/<int:restaurant_id>/<int:menu_id>/delete/',
	methods=['GET', 'POST'])
def delete_menu_item(restaurant_id, menu_id):
	if not is_logged():
		return redirect('login')

	menu = mnu_dao.get_menu(menu_id)
	if not is_owners_session(menu):
		return "<script>function f(){alert('You are not authorized to delete this menu item.');};</script><body onload='f()'></body>"

	if request.method == 'GET':
		rst_name = rst_dao.get_restaurant(restaurant_id).name
		return render_template(
			'deletemenuitem.html',
			menu = menu, 
			restaurant_name = rst_name,
			username = login_session['username'])

	# Else it's a POST
	mnu_dao.delete_menu(menu_id)
	flash('Menu item deleted', 'success')
	return redirect(url_for('show_menu', restaurant_id = restaurant_id))


if __name__ == '__main__':
	app.secret_key = 'super_insecure_key'
	app.debug = True
	app.run(host = '0.0.0.0', port = 5000)