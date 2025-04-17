import requests
from flask import Blueprint, request, render_template, redirect, url_for, session
from functools import wraps
from CTFd.plugins.challenges import BaseChallenge
from CTFd.utils.modes import TEAMS_MODE, get_mode_as_word, get_model
from CTFd.utils import get_config, set_config
from CTFd.utils.decorators import admins_only
from CTFd.models import Solves, db
from CTFd.utils.logging import log


def escape_markdown(text):
    badchars = [
        "\\",
        "_",
        "*",
        "[",
        "]",
        "(",
        ")",
        "~",
        "`",
        ">",
        "#",
        "+",
        "-",
        "=",
        "|",
        "{",
        "}",
        ".",
        "!",
    ]
    for char in badchars:
        text = text.replace(char, "\\" + char)
    return text


def notify_first_blood(solver_name, challenge_name):
    token = get_config("notifier_telegram_bot_token")
    chat_id = get_config("notifier_telegram_chat_id")

    if not token or not chat_id:
        return

    text = f"🩸 *Первая кровь!*\n{solver_name} первым решил задачу {challenge_name}!"

    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
    )


def load(app):
    tg_notify = Blueprint("tg_first_blood", __name__, template_folder="templates")

    @tg_notify.route("/admin/tg_first_blood", methods=["GET", "POST"])
    @admins_only
    def admin():
        errors = []

        if request.method == "POST":
            token = request.form.get("notifier_telegram_bot_token")
            chat_id = request.form.get("notifier_telegram_chat_id")
            set_config("notifier_telegram_bot_token", token)
            set_config("notifier_telegram_chat_id", chat_id)

            if "test_message" in request.form:
                if not token or not chat_id:
                    errors.append("Укажите токен и chat ID перед отправкой теста.")
                else:
                    try:
                        test_text = (
                            "*Тестовое сообщение*\nЕсли ты это видишь — всё работает ✅"
                        )

                        requests.post(
                            f"https://api.telegram.org/bot{token}/sendMessage",
                            json={"chat_id": chat_id, "text": test_text},
                        )
                    except Exception as e:
                        errors.append(f"Ошибка при отправке: {e}")

            return redirect(url_for("tg_first_blood.admin"))

        context = {
            "nonce": session.get("nonce"),
            "errors": errors,
            "notifier_telegram_bot_token": get_config("notifier_telegram_bot_token"),
            "notifier_telegram_chat_id": get_config("notifier_telegram_chat_id"),
        }
        return render_template("admin.html", **context)

    app.register_blueprint(tg_notify)

    def chal_solve_decorator(chal_solve_func):
        @wraps(chal_solve_func)
        def wrapper(user, team, challenge, request):
            chal_solve_func(user, team, challenge, request)

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
                solver_url = (
                    url_for("teams.public", team_id=solver.account_id, _external=True)
                    if get_mode_as_word() == TEAMS_MODE
                    else url_for(
                        "users.public", user_id=solver.account_id, _external=True
                    )
                )
                challenge_url = url_for(
                    "challenges.listing",
                    _external=True,
                    _anchor=f"{challenge.name}-{challenge.id}",
                )
                notify_first_blood(solver.name, challenge.name)

        return wrapper

    BaseChallenge.solve = chal_solve_decorator(BaseChallenge.solve)
