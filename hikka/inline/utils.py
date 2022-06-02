import asyncio
import inspect
import logging
import contextlib
import io
import os
from copy import deepcopy
from types import FunctionType
from typing import List, Optional, Union
from urllib.parse import urlparse

from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAnimation,
    InputMediaDocument,
    InputMediaAudio,
    InputMediaPhoto,
    InputMediaVideo,
    InputFile,
)

from aiogram.utils.exceptions import (
    InvalidQueryID,
    MessageIdInvalid,
    MessageNotModified,
    RetryAfter,
)

from .. import utils
from .._types import Module
from .types import InlineUnit

logger = logging.getLogger(__name__)


class Utils(InlineUnit):
    def _generate_markup(
        self,
        markup_obj: Union[str, list],
    ) -> Union[None, InlineKeyboardMarkup]:
        """Generate markup for form or list of `dict`s"""
        if not markup_obj:
            return None

        markup = InlineKeyboardMarkup()

        map_ = (
            self._units[markup_obj]["buttons"]
            if isinstance(markup_obj, str)
            else markup_obj
        )

        map_ = self._normalize_markup(map_)

        setup_callbacks = False

        for row in map_:
            for button in row:
                if not isinstance(button, dict):
                    logger.error(f"Button {button} is not a `dict`, but `{type(button)}` in {map_}")  # fmt: skip
                    return None

                if "callback" in button and "_callback_data" not in button:
                    button["_callback_data"] = utils.rand(30)
                    setup_callbacks = True

                if "input" in button and "_switch_query" not in button:
                    button["_switch_query"] = utils.rand(10)

        for row in map_:
            line = []
            for button in row:
                try:
                    if "url" in button:
                        if not utils.check_url(button["url"]):
                            logger.warning(
                                "Button have not been added to form, "
                                "because its url is invalid"
                            )
                            continue

                        line += [
                            InlineKeyboardButton(
                                button["text"],
                                url=button["url"],
                            )
                        ]
                    elif "callback" in button:
                        line += [
                            InlineKeyboardButton(
                                button["text"],
                                callback_data=button["_callback_data"],
                            )
                        ]
                        if setup_callbacks:
                            self._custom_map[button["_callback_data"]] = {
                                "handler": button["callback"],
                                **(
                                    {"always_allow": button["always_allow"]}
                                    if button.get("always_allow", False)
                                    else {}
                                ),
                                **(
                                    {"args": button["args"]}
                                    if button.get("args", False)
                                    else {}
                                ),
                                **(
                                    {"kwargs": button["kwargs"]}
                                    if button.get("kwargs", False)
                                    else {}
                                ),
                                **(
                                    {"force_me": True}
                                    if button.get("force_me", False)
                                    else {}
                                ),
                                **(
                                    {"disable_security": True}
                                    if button.get("disable_security", False)
                                    else {}
                                ),
                            }
                    elif "input" in button:
                        line += [
                            InlineKeyboardButton(
                                button["text"],
                                switch_inline_query_current_chat=button["_switch_query"] + " ",  # fmt: skip
                            )
                        ]
                    elif "data" in button:
                        line += [
                            InlineKeyboardButton(
                                button["text"],
                                callback_data=button["data"],
                            )
                        ]
                    elif "switch_inline_query_current_chat" in button:
                        line += [
                            InlineKeyboardButton(
                                button["text"],
                                switch_inline_query_current_chat=button[
                                    "switch_inline_query_current_chat"
                                ],
                            )
                        ]
                    elif "switch_inline_query" in button:
                        line += [
                            InlineKeyboardButton(
                                button["text"],
                                switch_inline_query_current_chat=button[
                                    "switch_inline_query"
                                ],
                            )
                        ]
                    else:
                        logger.warning(
                            "Button have not been added to "
                            "form, because it is not structured "
                            f"properly. {button}"
                        )
                except KeyError:
                    logger.exception(
                        "Error while forming markup! Probably, you "
                        "passed wrong type combination for button. "
                        "Contact developer of module."
                    )
                    return False

            markup.row(*line)

        return markup

    generate_markup = _generate_markup

    async def check_inline_security(
        self,
        *,
        func: FunctionType,
        user: int,
    ) -> bool:
        """Checks if user with id `user` is allowed to run function `func`"""
        return await self._client.dispatcher.security.check(
            func=func,
            user=user,
            message=None,
        )

    def _find_caller_sec_map(self) -> Union[FunctionType, None]:
        try:
            for stack_entry in inspect.stack():
                if hasattr(stack_entry, "function") and (
                    stack_entry.function.endswith("cmd")
                    or stack_entry.function.endswith("_inline_handler")
                ):
                    logger.debug(f"Found caller: {stack_entry.function}")
                    return next(
                        lambda: self._client.dispatcher.security.get_flags(
                            getattr(
                                cls_,
                                stack_entry.function,
                            ),
                        )
                        for name, cls_ in stack_entry.frame.f_globals.items()
                        if name.endswith("Mod") and issubclass(cls_, Module)
                    )
        except Exception:
            logger.debug("Can't parse security mask in form", exc_info=True)

        return None

    def _normalize_markup(self, reply_markup: Union[dict, list]) -> list:
        if isinstance(reply_markup, dict):
            return [[reply_markup]]

        if isinstance(reply_markup, list) and any(
            isinstance(i, dict) for i in reply_markup
        ):
            return [reply_markup]

        return reply_markup

    async def _edit_unit(
        self,
        text: str,
        reply_markup: List[List[dict]] = None,
        *,
        photo: Optional[str] = None,
        file: Optional[str] = None,
        video: Optional[str] = None,
        audio: Optional[str] = None,
        gif: Optional[str] = None,
        mime_type: Optional[str] = None,
        force_me: Union[bool, None] = None,
        disable_security: Union[bool, None] = None,
        always_allow: Union[List[int], None] = None,
        disable_web_page_preview: bool = True,
        query: CallbackQuery = None,
        unit_uid: str = None,
        inline_message_id: Union[str, None] = None,
    ):
        """Do not edit or pass `self`, `query`, `unit_uid` params, they are for internal use only"""
        if isinstance(reply_markup, (list, dict)):
            reply_markup = self._normalize_markup(reply_markup)
        elif reply_markup is None:
            reply_markup = [[]]

        if not isinstance(text, str):
            logger.error("Invalid type for `text`")
            return False

        if photo and (not isinstance(photo, str) or not utils.check_url(photo)):
            logger.error("Invalid type for `photo`")
            return False

        if gif and (not isinstance(gif, str) or not utils.check_url(gif)):
            logger.error("Invalid type for `gif`")
            return False

        if file and (
            not isinstance(file, str, bytes, io.BytesIO)
            or (isinstance(file, str) and not utils.check_url(file))
        ):
            logger.error("Invalid type for `file`")
            return False

        if file and not mime_type:
            logger.error(
                "You must pass `mime_type` along with `file` field\n"
                "It may be either 'application/zip' or 'application/pdf'"
            )
            return False

        if video and (not isinstance(video, str) or not utils.check_url(video)):
            logger.error("Invalid type for `video`")
            return False

        if audio and (not isinstance(audio, str) or not utils.check_url(audio)):
            logger.error("Invalid type for `audio`")
            return False

        media_params = [
            photo is None,
            gif is None,
            file is None,
            video is None,
            audio is None,
        ]

        if media_params.count(False) > 1:
            logger.error("You passed two or more exclusive parameters simultaneously")
            return False

        if unit_uid is not None and unit_uid in self._units:
            unit = self._units[unit_uid]

            unit["buttons"] = reply_markup

            if isinstance(force_me, bool):
                unit["force_me"] = force_me

            if isinstance(disable_security, bool):
                unit["disable_security"] = disable_security

            if isinstance(always_allow, list):
                unit["always_allow"] = always_allow
        else:
            unit = {}

        inline_message_id = (
            inline_message_id
            or unit.get("inline_message_id", False)
            or query.inline_message_id
        )

        if not inline_message_id:
            logger.warning(
                "Attempted to edit message with no `inline_message_id`. "
                "Possible reasons:\n"
                "- Form was sent without buttons and due to "
                "the limits of Telegram API can't be edited\n"
                "- There is an in-userbot error, which you should report"
            )
            return False

        if all(media_params):
            try:
                await self.bot.edit_message_text(
                    text,
                    inline_message_id=inline_message_id,
                    disable_web_page_preview=disable_web_page_preview,
                    reply_markup=self.generate_markup(
                        reply_markup
                        if isinstance(reply_markup, list)
                        else unit.get("buttons", [])
                    ),
                )
            except MessageNotModified:
                if query:
                    with contextlib.suppress(InvalidQueryID):
                        await query.answer()
            except RetryAfter as e:
                logger.info(f"Sleeping {e.timeout}s on aiogram FloodWait...")
                await asyncio.sleep(e.timeout)
                return await self._edit_unit(**utils.get_kwargs())
            except MessageIdInvalid:
                with contextlib.suppress(Exception):
                    await query.answer(
                        "I should have edited some message, but it is deleted :("
                    )

            return

        # If passed `photo` is gif
        try:
            path = urlparse(media).path
            ext = os.path.splitext(path)[1]
        except Exception:
            ext = None

        if photo is not None and ext in {".gif", ".mp4"}:
            gif = deepcopy(photo)
            photo = None

        if file is not None:
            media = InputMediaDocument(file, caption=text, parse_mode="HTML")
        elif photo is not None:
            media = InputMediaPhoto(photo, caption=text, parse_mode="HTML")
        elif audio is not None:
            media = InputMediaAudio(audio, caption=text, parse_mode="HTML")
        elif video is not None:
            media = InputMediaVideo(video, caption=text, parse_mode="HTML")
        elif gif is not None:
            media = InputMediaAnimation(gif, caption=text, parse_mode="HTML")

        try:
            await self.bot.edit_message_media(
                inline_message_id=inline_message_id,
                media=media,
                reply_markup=self.generate_markup(
                    reply_markup
                    if isinstance(reply_markup, list)
                    else unit.get("buttons", [])
                ),
            )
        except RetryAfter as e:
            logger.info(f"Sleeping {e.timeout}s on aiogram FloodWait...")
            await asyncio.sleep(e.timeout)
            return await self._edit_unit(**utils.get_kwargs())
        except MessageIdInvalid:
            with contextlib.suppress(Exception):
                await query.answer(
                    "I should have edited some message, but it is deleted :("
                )

    async def _delete_unit_message(
        self,
        call: CallbackQuery = None,
        unit_uid: str = None,
    ) -> bool:
        """Params `self`, `form`, `unit_uid` are for internal use only, do not try to pass them"""
        try:
            await self._client.delete_messages(
                self._units[unit_uid]["chat"],
                [self._units[unit_uid]["message_id"]],
            )

            await self._unload_unit(None, unit_uid)
        except Exception:
            return False

        return True

    async def _unload_unit(
        self,
        call: CallbackQuery = None,
        unit_uid: str = None,
    ) -> bool:
        """Params `self`, `unit_uid` are for internal use only, do not try to pass them"""
        try:
            if "on_unload" in self._units[unit_uid] and callable(
                self._units[unit_uid]["on_unload"]
            ):
                self._units[unit_uid]["on_unload"]()

            if unit_uid in self._units:
                del self._units[unit_uid]
            else:
                return False
        except Exception:
            return False

        return True
