import requests
import threading, time
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

            notify_timing = "notify_timing" in request.form
            set_config("notify_timing", notify_timing)
            ctf_start_text = request.form.get("ctf_start_text").strip()
            set_config("ctf_start_text", ctf_start_text)
            ctf_end_text = request.form.get("ctf_end_text").strip()
            set_config("ctf_end_text", ctf_end_text)
            ctf_warn_minutes = int(request.form.get("ctf_warn_minutes"))
            set_config("ctf_warn_minutes", ctf_warn_minutes)
            ctf_warn_start_text = request.form.get("ctf_warn_start_text").strip()
            set_config("ctf_warn_start_text", ctf_warn_start_text)
            ctf_warn_end_text = request.form.get("ctf_warn_end_text").strip()
            set_config("ctf_warn_end_text", ctf_warn_end_text)

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
            "notify_timing": get_config("notify_timing", False),
            "ctf_start_text": get_config("ctf_start_text", "CTF started!"),
            "ctf_end_text": get_config("ctf_end_text", "CTF ended!"),
            "ctf_warn_minutes": get_config("ctf_warn_minutes", 15),
            "ctf_warn_start_text": get_config(
                "ctf_warn_start_text",
                "CTF starts in {minutes} minutes!",
            ),
            "ctf_warn_end_text": get_config(
                "ctf_warn_end_text", "CTF ends in {minutes} minutes!"
            ),
        }
        return render_template("admin.html", **context)

    app.register_blueprint(notify)

    def chal_solve_decorator(chal_solve_func):
        @wraps(chal_solve_func)
        def wrapper(user, team, challenge, request):
            chal_solve_func(user, team, challenge, request)

            notify_firstblood = get_config("notify_firstblood", False)
            if not notify_firstblood:
                return

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

    start_scheduler(app)

    BaseChallenge.solve = chal_solve_decorator(BaseChallenge.solve)


def start_scheduler(app):
    def check_ctf_times():
        flags = {
            "start_sent": False,
            "end_sent": False,
            "warn_start_sent": False,
            "warn_end_sent": False,
        }

        with app.app_context():
            while True:
                now = int(time.time())
                start_time = int(get_config("start") or 0)
                end_time = int(get_config("end") or 0)
                warn_minutes = int(get_config("ctf_warn_minutes", 15))
                notify_timing = get_config("notify_timing", False)

                if not notify_timing:
                    return

                if (
                    start_time
                    and (start_time - warn_minutes * 60 <= now < start_time)
                    and not flags["warn_start_sent"]
                ):
                    send_notify(
                        "CTF start",
                        get_config(
                            "ctf_warn_start_text", "CTF starts in {minutes} minutes!"
                        ).format(minutes=warn_minutes),
                    )
                    flags["warn_start_sent"] = True

                if start_time and now >= start_time and not flags["start_sent"]:
                    send_notify(
                        "CTF start", get_config("ctf_start_text", "CTF started!")
                    )
                    flags["start_sent"] = True

                if (
                    end_time
                    and (end_time - warn_minutes * 60 <= now < end_time)
                    and not flags["warn_end_sent"]
                ):
                    send_notify(
                        "CTF end",
                        get_config(
                            "ctf_warn_end_text", "CTF ends in {minutes} minutes!"
                        ).format(minutes=warn_minutes),
                    )
                    flags["warn_end_sent"] = True

                if end_time and now >= end_time and not flags["end_sent"]:
                    send_notify("CTF end", get_config("ctf_end_text", "CTF ended!"))
                    flags["end_sent"] = True

                time.sleep(60)

    def send_notify(title, message):
        if message:
            send_notify_telegram(message)
            send_notify_ctfd(title, message)

    scheduler_thread = threading.Thread(target=check_ctf_times, daemon=True)
    scheduler_thread.start()
