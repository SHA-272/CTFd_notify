import requests
from flask import (
    Blueprint,
    request,
    render_template,
    redirect,
    url_for,
    session,
    current_app,
)
from functools import wraps
from CTFd.plugins.challenges import BaseChallenge
from CTFd.utils.modes import TEAMS_MODE, get_mode_as_word, get_model
from CTFd.utils import get_config, set_config
from CTFd.utils.decorators import admins_only
from CTFd.models import Solves, db
from CTFd.schemas.notifications import NotificationSchema
from CTFd.utils.logging import log


# Send notification to Telegram
def send_notify_telegram(text):
    token = get_config("telegram_bot_token")
    admin_id = get_config("telegram_admin_id")
    chat_id = get_config("telegram_chat_id")
    notify_telegram = get_config("notify_telegram", False)

    if not token and not notify_telegram:
        return

    try:

        if admin_id:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": admin_id, "text": text},
            )

        if chat_id:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
    except Exception as e:
        log(
            "telegram",
            f"Failed to send message to Telegram: {e}",
        )


# Send notification to CTFd
def send_notify_ctfd(title, text):
    notify_ctfd = get_config("notify_ctfd", False)
    if not notify_ctfd:
        return

    data = {
        "title": title,
        "content": text,
        "type": "toast",
        "sound": True,
    }

    schema = NotificationSchema()
    result = schema.load(data)

    if result.errors:
        return {"success": False, "errors": result.errors}, 400

    db.session.add(result.data)
    db.session.commit()

    response = schema.dump(result.data)

    notif_type = data.get("type", "alert")
    notif_sound = data.get("sound", True)
    response.data["type"] = notif_type
    response.data["sound"] = notif_sound

    current_app.events_manager.publish(data=response.data, type="notification")


def load(app):
    notify = Blueprint("notify", __name__, template_folder="templates")

    @notify.route("/admin/notify", methods=["GET", "POST"])
    @admins_only
    def admin():
        if request.method == "POST":
            notify_ctfd = "notify_ctfd" in request.form
            set_config("notify_ctfd", notify_ctfd)
            notify_telegram = "notify_telegram" in request.form
            set_config("notify_telegram", notify_telegram)

            token = request.form.get("telegram_bot_token")
            set_config("telegram_bot_token", token)
            admin_id = request.form.get("telegram_admin_id")
            set_config("telegram_admin_id", admin_id)
            chat_id = request.form.get("telegram_chat_id")
            set_config("telegram_chat_id", chat_id)

            notify_firstblood = "notify_firstblood" in request.form
            set_config("notify_firstblood", notify_firstblood)
            firstblood_text = request.form.get("firstblood_text").strip()
            set_config("firstblood_text", firstblood_text)

            if "test_message" in request.form:
                test_text = "✅"
                send_notify_telegram(test_text)

            return redirect(url_for("notify.admin"))

        context = {
            "nonce": session.get("nonce"),
            "notify_ctfd": get_config("notify_ctfd", False),
            "notify_telegram": get_config("notify_telegram", False),
            "telegram_bot_token": get_config("telegram_bot_token"),
            "telegram_admin_id": get_config("telegram_admin_id"),
            "telegram_chat_id": get_config("telegram_chat_id"),
            "notify_firstblood": get_config("notify_firstblood", False),
            "firstblood_text": get_config(
                "firstblood_text", "First task solve {challenge} by {solver}!"
            ),
        }
        return render_template("admin.html", **context)

    app.register_blueprint(notify)

    # First blood notification
    def chal_solve_decorator(chal_solve_func):
        @wraps(chal_solve_func)
        def wrapper(user, team, challenge, request):
            chal_solve_func(user, team, challenge, request)

            notify_firstblood = get_config("notify_firstblood", False)
            if not notify_firstblood:
                return wrapper

            Model = get_model()
            solve_count = (
                db.session.query(db.func.count(Solves.id))
                .filter(Solves.challenge_id == challenge.id)
                .join(Model, Solves.account_id == Model.id)
                .filter(Model.banned == False, Model.hidden == False)
                .scalar()
            )
            if solve_count == 1:
                solver = team if get_mode_as_word() == TEAMS_MODE else user

                text = get_config("firstblood_text").format(
                    solver=solver.name[:256], challenge=challenge.name[:256]
                )

                send_notify_ctfd("First Blood", text)
                send_notify_telegram(text)

        return wrapper

    BaseChallenge.solve = chal_solve_decorator(BaseChallenge.solve)
