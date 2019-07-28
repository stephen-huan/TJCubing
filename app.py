import time, os, logging, traceback
from datetime import datetime
import flask
from flask_sitemap import Sitemap
import flask_uploads
from werkzeug.utils import secure_filename
from werkzeug.exceptions import InternalServerError
from werkzeug.wrappers.response import Response
from flask_talisman import Talisman
from flask_wtf.csrf import CSRFProtect
import cube, forms, statistics

# TODO: in house comps page
# TODO: partial highlighting on mobile
# TODO: fix mobile login/profile UI
# TODO: 413 not being rendered in actual site
# TODO: general security, enable autoescaping
# print([rule.endpoint for rule in app.url_map.iter_rules()])

def select_jinja_autoescape(self, filename: str) -> bool:
    """ Overwrites Flask's default behavior to autoescape on custom file endings. """
    if filename is None:
        return False
    if filename.endswith(cube.FILE):
        return True
    return flask.Flask.select_jinja_autoescape(self, filename)

# flask.Flask.select_jinja_autoescape = select_jinja_autoescape
app = flask.Flask(__name__)

# config: one default (public), the other private
app.config.from_object('settings')
os.environ["FLASK_SETTINGS"] = cube.CONFIG["flask_config"]
app.config.from_envvar("FLASK_SETTINGS")

# Security
csp = {
    "default-src": "'self'",
}
Talisman(app, content_security_policy=None)
# explicit CSRF protection for all forms
CSRFProtect(app)

# generate sitemap
# ext = Sitemap(app=app)

flask_uploads.configure_uploads(app, (forms.times, forms.photos))
# Strange glitch with TJ servers - if you look at the source code this *should* happen already...
app.register_blueprint(flask_uploads.uploads_mod)

def alert(msg: str, category: str="success", loc: str="index") -> Response:
    """ Redirects the user with an alert. """
    flask.flash(msg, category)
    dest = {"self": flask.request.path,
            "meta": flask.request.full_path
           }
    return flask.redirect(flask.url_for(loc) if loc not in dest else dest[loc])

@app.before_request
def before_request() -> None:
    """ Runs before each request. """
    try:
        vote = cube.load_file("vote")
    except:
        #sometimes fails, not sure why
        app.logger.warning("Error in parsing JSON file")
    else:
        if time.time() > cube.short_date(vote["ends_at"]):
            vote["vote_active"] = False
            cube.dump_file(vote, "vote")

# @app.after_request
# def do_something_whenever_a_request_has_been_handled(response):
#     # we have a response to manipulate, always return one
#     return response

def index() -> dict:
    """ Gives the year to list past TJHSST competitions, handles email signups. """
    form = forms.EmailForm()

    if form.validate_on_submit():
        cube.prompt_email(form.email.data)
        return alert("Check your email for a verification message.", "success")

    return {"year": cube.get_year(), "form": form}

def competitions() -> dict:
    """ Gets the cached competitions, but will refresh if not updated recently. """
    c = cube.load_file("comps")
    last, comps = c["time"], c["comps"]
    if time.time() - last > cube.CONFIG["time"]:
        comps = cube.get_comps()

    return {"comps": comps, "last": cube.unix_to_human(last), "icons": cube.ICONS}

def lectures() -> dict:
    """ Returns the lectures. """
    return {"lectures": cube.get_lectures()}

def result() -> dict:
    """ Displays the result of the election. """
    return {"result": cube.get_winner(), "vote": cube.load_file("vote")}

# http://flask.pocoo.org/docs/1.0/patterns/fileuploads/
def stats() -> dict:
    """ Parses a user's .csv / .txt file and returns statistics. """
    form, file = forms.StatsForm(), forms.UploadForm()
    rtn = {"form": form, "file": file}

    if form.validate_on_submit():
        times = statistics.parse_text(form.times.data)
    elif file.validate_on_submit():
        times = statistics.parse(file.file.data)
    else:
        times = None

    if times:
        descr, mean, best = statistics.process(times)
        return cube.add_dict({"descr": descr, "mean": mean, "best": best}, rtn)

    return rtn

def profile() -> dict:
    """ Allows the user to login as well as register a new account. """
    loginForm = forms.LoginForm()
    signupForm = forms.SignupForm()
    mailForm = forms.MailForm()
    httpForm = forms.HTTPForm()
    ionForm, wcaForm = forms.APIForm(prefix="ion"), forms.APIForm(prefix="wca")
    rtn = {"loginForm": loginForm, "signupForm": signupForm, "mailForm": mailForm, "httpForm": httpForm, "ionForm": ionForm, "wcaForm": wcaForm}

    users = cube.load_file("users")

    if "account" in flask.session:
        tabs = [["overview", "API"], ["email", "refresh", "develop"], ["edit"]]
        scopes = {"default": 0, "privileged": 1, "admin": 2}
        tab = flask.request.args.get('tab', 'overview')
        scope = scopes[flask.session["scope"]]

        i = None
        for j, group in enumerate(tabs):
            if tab in group:
                i = j
        if i is None:
            return alert("Invalid tab!", "info", "self")
        if i > scope:
            return alert("User does not have the valid scope. This incident will be logged.", "danger", "self")

        rtn = cube.add_dict(
               {"tabs": tabs,
                "scopes": scopes,
                "clubmailpassword": cube.load_file("secrets")["clubmailpassword"],
                "emails": cube.load_file("emails")["emails"],
               }, rtn)
    else:
        scope = 0

    if scope >= 0:
        if "confirm" in flask.request.form and signupForm.validate_on_submit():
            username, password = signupForm.username.data, signupForm.password.data
            if username in cube.load_file("users"):
                return alert("Username is taken.", "info", "meta")
            if password != signupForm.confirm.data:
                return alert("Passwords do not match.", "info", "meta")

            cube.register(username, password)
            return alert("Account registered!", "success", "self")

        elif "login" in flask.request.form and loginForm.validate_on_submit():
            username, password = loginForm.username.data, loginForm.password.data
            if not cube.check(username, password):
                return alert("Username or password is incorrect.", "info", "self")

            # Save login to cookies
            flask.session["account"] = username
            flask.session["scope"] = users[username]["scope"]
            return flask.redirect(flask.url_for("profile"))

        elif ionForm.validate_on_submit():
            rtn = cube.add_dict({"data": cube.api_call("ion", ionForm.call.data)}, rtn)

        elif wcaForm.validate_on_submit():
            rtn = cube.add_dict({"data": cube.api_call("wca", wcaForm.call.data)}, rtn)

    if scope >= 1:
        if mailForm.validate_on_submit():
            recipients = mailForm.recipients.data.split(", ") if mailForm.recipients.data != "" else cube.load_file("emails")["emails"]
            body = cube.markdown2.markdown(mailForm.email.data).replace("\n", "")
            cube.send_email(recipients, mailForm.subject.data, body)
            if mailForm.log.data:
                cube.save_email(mailForm.subject.data, mailForm.email.data)
            return alert("Mail sent.", "success", "meta")

        elif httpForm.validate_on_submit():
            flask.abort(int(httpForm.http.data))

    if scope >= 2:
        pass

    if flask.request.method == "POST":

        if "logout" in flask.request.form:
            del flask.session["account"]

        if "delete" in flask.request.form:
            del users[flask.session["account"]]
            del flask.session["account"]
            cube.dump_file(users, "users")
            return alert("Account deleted!", "success", "self")

        if "clear" in flask.request.form:
            # Save CSRF token
            csrf = flask.session["csrf_token"]
            flask.session.clear()
            flask.session["csrf_token"] = csrf

        if "fb" in flask.request.form:
            cube.get_pfps(cube.CONFIG["officers"])

        if "comps" in flask.request.form:
            cube.get_comps()

        if "records" in flask.request.form:
            cube.update_records()

    return rtn

def delete_photo() -> None:
    """ Deletes a photo from the server. """
    users = cube.load_file("users")
    user = users[flask.session["account"]]
    try:
        del user["pfp"]
    except KeyError:
        pass

    try:
        os.remove(forms.photos.path(user["pfpfilename"]))
        del user["pfpfilename"]
    except (KeyError, FileNotFoundError):
        pass

    cube.dump_file(users, "users")

def settings() -> dict:
    """ Adjusts a user's profile. """
    if "account" not in flask.session:
        return flask.redirect("profile")

    users = cube.load_file("users")
    user = users[flask.session["account"]]
    gpgForm = forms.GPGForm()
    photoForm = forms.PhotoForm()

    if gpgForm.validate_on_submit():
        key = cube.gpg.import_keys(gpgForm.gpgkey.data)
        user["keys"] += cube.gpg.list_keys(keys=[key.fingerprints[0]])
        curr = user["keys"][-1]
        curr["fuids"] = ", ".join([uid.split()[-1][1:-1] for uid in curr["uids"]])
        cube.dump_file(users, "users")
        return alert("GPG key added.", "success", "meta")

    elif photoForm.validate_on_submit():
        users = cube.load_file("users")
        delete_photo() #old profile picture not necessary anymore
        filename = forms.photos.save(photoForm.photo.data)
        users[flask.session["account"]]["pfp"] = forms.photos.url(filename)
        users[flask.session["account"]]["pfpfilename"] = filename
        cube.dump_file(users, "users")
        return alert("Profile photo changed.", "success", "profile")

    if flask.request.method == "POST":
        if "remove" in flask.request.form:
            delete_photo()
            return alert("Profile picture removed.", "success", "profile")

        if "delete" in flask.request.form:
            del user["keys"][int(flask.request.form["delete"])]
            cube.dump_file(users, "users")

    return {"gpgForm": gpgForm, "photoForm": photoForm}

def records() -> dict:
    """ Displays TJ's all time bests. """
    records = cube.load_file("records")
    times, people = records["records"], records["people"]

    if "wca_token" in flask.session and "ion_token" in flask.session:
        me = cube.api_call("wca", "me")["me"]
        year = cube.api_call("ion", "profile")["graduation_year"]
        refresh = False

        if [me["url"], me["name"], year] not in people:
            records["people"].append([me["url"], me["name"], year])
            # New person added
            refresh = True
            cube.dump_file(records, "records")

        if refresh or time.time() - records["time"] > cube.CONFIG["time"]:
            cube.update_records()

    return {"times": times, "events": cube.EVENTS, "icons": cube.ICONS, "DNF": statistics.DNF, "ranks": cube.RANKS}

def rankings() -> dict:
    """ Shows the rankings of all TJ students signed up. """
    return {"records": cube.load_file("records")["records"], "events": cube.EVENTS}

def search() -> dict:
    """ Parses the user's search. Can be POST or GET method. """
    form = forms.SearchForm()
    if form.validate_on_submit():
        query = form.query.data
    elif flask.request.args.get("query", None) is not None:
        query = flask.request.args["query"]
    else:
        return {}
    return {"entries": [(time, NAMES[html], html, preview) for time, html, preview in cube.parse_search(query)]}

def convert(order:list, prefix:str="", new:list=[]) -> list:
    """ Converts an order specification into a parsable (path, name) format by the nav header. """
    for name in order:
        if isinstance(name, list):
            addition, sublist = name
            new.append(("*" + addition, convert(sublist, "{}".format(prefix), [])))
        else:
            new.append(("{}{}".format(prefix, name if name != "" else "index"), NAMES[name]))
    return new

params = cube.load_file("site")
TSLASH = "/" if params["tailing_slash"] else ""
NAMES = params["names"]
NAV = convert(params["order"])

PAGES = {"": (index, ["POST", "GET"]),
         "competitions": competitions,
         "algorithms": None,
         "weekly": {
             "lectures": lectures,
             "inhouse": None,
             },
         "contact": lambda: {"fb": cube.load_file("fb")},
         "archive":
             {
                "emails": lambda: {"emails": [{k: v if k != "body" else cube.markdown2.markdown(v) for k, v in email.items()}
                                              for email in cube.load_file("mails.json", "json", False)
                                             ]
                                  },
                "history": lambda: cube.parse_club(),
                "tips": None,
             },
         "vote":
             {
                "eligibility": lambda: cube.load_file("vote"),
                "admission": lambda: {"admission": cube.open_admission(), "sigs": cube.get_sigs()},
                "result": result,
             },
         "misc":
             {
                "stats": (stats, ["POST", "GET"]),
             },
         "results":
            {
                "rankings": rankings,
                "records": records,
            },
         "profile": (profile, ["POST", "GET"]),
         "settings": (settings, ["POST", "GET"]),
         "search": (search, ["POST", "GET"])
        }

### Jinja ###

@app.template_filter()
def capitalize(s: str) -> str:
    """ Capitalizes the first character while keeping the rest the same case.
        Replaces Jinja's default filter. """
    return s[0].upper() + s[1:]

def URL() -> str:
    """ Dynamically generates a URL based on the request. """
    return params["url"] if cube.https else flask.request.url_root[:-1]

def furl_for(endpoint: str, filename: str=None, **kwargs: dict) -> str:
    """ Replacement for url_for. """
    return URL() + (flask.url_for(endpoint, filename=filename) if filename != None else ("/" if endpoint == "" else flask.url_for(endpoint, **kwargs)))

def furl(filename: str) -> str:
    """ Assumes a static endpoint. """
    return furl_for('static', filename)

def expose(d: dict, l: list) -> None:
    """ Adds functions to a dictionary. """
    for f in l:
        d[f.__name__] = f

# Basically a cache of variables that don't change
GLOBAL = {"pages": NAV,
          "URL": params["url"],
          "repo": cube.REPO,
          "TJ": cube.TJ,
          "updated": cube.github_commit_time(),
          "humanize": cube.humanize,
          "arrow": cube.arrow,
          "clubmail": cube.CONFIG["clubmail"],
          "wca_time": cube.time_formatted
         }

expose(GLOBAL, [furl, furl_for])

@app.context_processor
def GLOBALS() -> dict:
    """ Returns all the global variables passed to every template. """
    user = cube.load_file("users").get(flask.session.get("account", None), {})
    vars = {"vote_active": cube.load_file("vote")["vote_active"],
            "user": user,
            "btnform": forms.FlaskForm(),
            "searchForm": forms.SearchForm(),
           }
    return cube.add_dict(GLOBAL, vars)

### Pages ###

def make_page(s: str, f=lambda: {}, methods=['GET']):
    """ Takes in a string which specifies both the url and the file name, as well as a function which provides the kwargs for render_template. """
    title = s.split("/")[-1]
    def func():
        val = f()
        if isinstance(val, dict):
            return flask.render_template((s if s != "" else "index") + cube.FILE, title=NAMES[title], **val)
        return val
    # Need distinct function names for Flask not to error
    func.__name__ = title if s != "" else "index"
    return app.route(("/{}" + TSLASH).format(s), methods=methods)(func)

def make_pages(d: dict, prefix="") -> None:
    """ Makes the entire site's pages. Recurs on nested dicts, taking file structure into account. """
    for key, value in d.items():
        if isinstance(value, dict):
            make_pages(value, prefix + "{}/".format(key))
        else:
            func, methods = ((value, ["GET"]) if not isinstance(value, tuple) else value)
            #don't need to store created function as it's already bound to the URL
            make_page(prefix + key, func if func is not None else lambda: {}, methods)

# https://stackoverflow.com/questions/14048779/with-flask-how-can-i-serve-robots-txt-and-sitemap-xml-as-static-files
@app.route("/sitemap.xml")
@app.route("/robots.txt")
def static_from_root() -> flask.wrappers.Response:
    """ Serves a file from static, skipping the /static/. """
    return flask.send_from_directory(app.static_folder, flask.request.path[1:])

@app.route("/cookie")
def cookie() -> dict:
    """ Returns the user's cookies. """
    data = dict(flask.session)
    users = cube.load_file("users")
    if "account" in flask.session:
        user = users[flask.session["account"]]
        if len(user["keys"]) > 0:
            return str(cube.gpg.encrypt(flask.json.dumps(data), [k["fingerprint"] for k in user["keys"]], always_trust=True))
    return data

@app.route("/email")
def email() -> Response:
    """ After requesting to be added to the email list, see if nonce matches. """
    emails = cube.load_file("emails")
    requests = emails["requests"]
    nonce = flask.request.args.get("nonce", None)
    if nonce in requests:
        email = requests[nonce]
        # Remove previous attempts
        emails["requests"] = {nonce: value for nonce, value in requests.items() if value != email}
        cube.dump_file(emails, "emails")
        cube.register_email(email)
        return alert("You have been added to the email list.", "success")

    return alert("Wrong nonce. Try registering again?", "danger")

# https://requests-oauthlib.readthedocs.io/en/latest/
def make_login(name: str):
    """ Makes a login route for an Oauth API. """
    def login() -> Response:
        oauth = cube.make_oauth(name)
        authorization_url, state = oauth.authorization_url(cube.CONFIG[cube.make_key(name, "url")] + "oauth/authorize")

        # State is used to prevent CSRF, keep this for later.
        flask.session[cube.make_key(name, "state")] = state
        return flask.redirect(authorization_url)

    login.__name__ = cube.make_key(name, "login")
    return login

def make_callback(name: str):
    """ Makes a callback route for an Oauth API. """
    def callback() -> Response:
        key = cube.make_key(name, "state")
        if key not in flask.session:
            return alert("You shouldn't be here...", "danger")

        assert flask.request.args.get("state", None) == flask.session[key]
        code = flask.request.args.get("code", None)
        if flask.request.args.get("error", None) is not None or code is None:
            return alert("You must allow OAuth access for site functions!", "danger")

        cube.fetch_token(name, cube.make_oauth(name, state=flask.session[key]), code)

        if "action" in flask.session:
            action = flask.session["action"]
            del flask.session["action"]
        else:
            action = "profile"

        return flask.redirect(action)

    callback.__name__ = cube.make_key(name, "callback")
    return callback

def make_api(names: list) -> None:
    """ Makes login and corresponding callback pages for each API. """
    for name in names:
         app.route("/" + cube.make_key(name, "login"))(make_login(name))
         app.route("/" + cube.make_key(name, "callback"))(make_callback(name))

def vote_requirements():
    """ Checks if the user can view the vote/run pages. """
    if not cube.test_token("ion"):
        flask.session["action"] = flask.request.path
        return flask.redirect(flask.url_for("ion_login"))

    vote = cube.load_file("vote")

    if not cube.valid_voter():
        return alert("You do not fullfill the requirements to be able to vote.", "warning")

    if not vote["vote_active"]:
        return alert("Stop trying to subvert democracy!", "danger")

    return vote

@app.route("/vote/vote", methods=["GET", "POST"])
def vote():
    """ Allows the user to vote. """
    vote = vote_requirements()
    if not isinstance(vote, dict):
        return vote

    if flask.request.method == "POST":
        cube.add_vote(cube.get_name(), flask.request.form["vote"])
        return alert("<strong>Congrats!</strong> You have voted for {}.".format(flask.request.form["vote"]))

    return flask.render_template(flask.request.path + cube.FILE, **vote, sorted_candidates=cube.get_candidates(), name=cube.get_name(), title="vote")

@app.route("/vote/run", methods=["GET", "POST"])
def run():
    """ Allows the user to run for an officer position. """
    vote = vote_requirements()
    if not isinstance(vote, dict):
        return vote

    form = forms.RunForm()

    signups = cube.get_signups()
    params = {"name": cube.get_name(),
              "years": cube.count_years(signups),
              "meetings": cube.count_meetings(signups, datetime.min, datetime.max),
              "annual_meetings": cube.count_meetings(signups),
              "year": cube.get_year(),
             }

    if form.validate_on_submit():
        cube.store_candidate(cube.add_dict({"description": form.description.data}, params))
        return alert("<strong>Congrats!</strong> Your application has been registered.")

    return flask.render_template(flask.request.path + cube.FILE, **vote, **params, title="run", length=forms.LENGTH, form=form)

# http://flask.pocoo.org/docs/1.0/patterns/errorpages/
# Catch-all exception handler
@app.errorhandler(Exception)
def exception_handler(e):
    # app.logger.error("{}: {}".format(type(e).__name__, e))
    app.logger.error(traceback.format_exc())
    return make_error_page(500)(InternalServerError())

def make_error_page(error: int):
    """ Makes an error page. """
    def f(e):
        body = e.get_body().strip().split("\n")
        title, descr = body[1][7:-8], body[-1][3:-4]
        return flask.render_template('error/{}{}'.format(error, cube.FILE), title=title, descr=descr), error
    return f

def make_error_pages(errors: list) -> None:
    """ Makes error pages. """
    for error in errors:
        app.errorhandler(error)(make_error_page(error))

make_error_pages(cube.get_errors())
make_api(["ion", "wca"])
make_pages(PAGES)

# https://medium.com/@trstringer/logging-flask-and-gunicorn-the-manageable-way-2e6f0b8beb2f
if __name__ != '__main__':
    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)
