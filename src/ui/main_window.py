from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QThreadPool, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ai.client import AIClient, AIClientError, ChatMessage
from config.settings import AppSettings
from memory.notes_store import NotesStore
from safety.command_safety import CommandSafety, SafeCommandRunner
from tools.chat_actions import ChatAction, ChatActionRouter
from tools.launcher import Launcher, LauncherError
from tools.system_info import SystemInfoService
from ui.theme import APP_STYLESHEET
from ui.workers import FunctionWorker
from version import APP_RELEASE_NAME
from voice.speech_service import SpeechService
from voice.transcriber import VoiceTranscriber


class JarvixWindow(QMainWindow):
    def __init__(
        self,
        settings: AppSettings,
        ai_client: AIClient,
        notes_store: NotesStore,
        transcriber: VoiceTranscriber,
        speech_service: SpeechService,
        launcher: Launcher,
        system_info: SystemInfoService,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.ai_client = ai_client
        self.notes_store = notes_store
        self.transcriber = transcriber
        self.speech_service = speech_service
        self.launcher = launcher
        self.system_info = system_info
        self.command_runner = SafeCommandRunner(CommandSafety())
        self.chat_actions = ChatActionRouter()
        self.messages: list[ChatMessage] = []
        self.chat_transcript: list[str] = []
        self.thread_pool = QThreadPool.globalInstance()
        self.nav_buttons: list[QPushButton] = []

        self.setWindowTitle(APP_RELEASE_NAME)
        self.resize(1460, 820)
        self.setMinimumSize(1180, 720)
        self.setStyleSheet(APP_STYLESHEET)
        self._build_ui()
        self._refresh_notes()
        self._refresh_system_info()
        self._refresh_top_info()
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._refresh_top_info)
        self._clock_timer.start(30_000)
        self._set_status("idle")

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(242)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(22, 26, 18, 18)
        sidebar_layout.setSpacing(12)

        brand = QLabel("JARVIX")
        brand.setObjectName("Brand")
        subtitle = QLabel("local assistant v1")
        subtitle.setObjectName("SidebarSubtitle")
        sidebar_layout.addWidget(brand)
        sidebar_layout.addWidget(subtitle)
        sidebar_layout.addSpacing(16)

        self.stack = QStackedWidget()
        self.stack.setObjectName("ContentPanel")
        self._add_shadow(self.stack, blur=38, color="#05090f", y=14)
        sections: list[tuple[str, str, Callable[[], QWidget]]] = [
            ("Chat", "◉", self._build_chat_view),
            ("Voice", "⌁", self._build_voice_view),
            ("Notes", "▤", self._build_notes_view),
            ("Apps", "▦", self._build_apps_view),
            ("System", "▱", self._build_system_view),
            ("Settings", "⚙", self._build_settings_view),
        ]
        for index, (name, symbol, builder) in enumerate(sections):
            button = QPushButton(f"{symbol}   {name}")
            button.setProperty("nav", True)
            button.clicked.connect(lambda _checked=False, i=index: self._show_section(i))
            self.nav_buttons.append(button)
            sidebar_layout.addWidget(button)
            self.stack.addWidget(builder())

        sidebar_layout.addStretch(1)
        status_card = QFrame()
        status_card.setObjectName("SidebarCard")
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(14, 12, 14, 12)
        status_layout.setSpacing(4)
        status_title = QLabel("System Status")
        status_title.setObjectName("Caption")
        status_online = QLabel("Online")
        status_online.setObjectName("OnlineText")
        status_detail = QLabel("All systems operational")
        status_detail.setObjectName("MutedText")
        status_layout.addWidget(status_title)
        status_layout.addWidget(status_online)
        status_layout.addWidget(status_detail)
        sidebar_layout.addWidget(status_card)

        profile_card = QFrame()
        profile_card.setObjectName("ProfileCard")
        profile_layout = QHBoxLayout(profile_card)
        profile_layout.setContentsMargins(12, 10, 12, 10)
        profile_layout.setSpacing(10)
        avatar = QLabel("Z")
        avatar.setObjectName("ProfileAvatar")
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setFixedSize(38, 38)
        profile_text = QLabel("Zyad\nLocal Profile")
        profile_text.setObjectName("MutedText")
        profile_layout.addWidget(avatar)
        profile_layout.addWidget(profile_text, 1)
        sidebar_layout.addWidget(profile_card)

        self.status_label = QLabel()
        self.status_label.setObjectName("StatusPill")
        sidebar_layout.addWidget(self.status_label)

        layout.addWidget(sidebar)
        layout.addWidget(self.stack, 1)
        self._add_shadow(sidebar, blur=32, color="#05090f", y=12)
        self.right_rail = self._build_right_rail()
        layout.addWidget(self.right_rail)
        self.setCentralWidget(root)
        self._show_section(0)

    def _build_right_rail(self) -> QFrame:
        rail = QFrame()
        rail.setObjectName("RightRail")
        rail.setFixedWidth(266)
        self._add_shadow(rail, blur=32, color="#05090f", y=12)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        time_card = QFrame()
        time_card.setObjectName("UtilityCard")
        time_layout = QHBoxLayout(time_card)
        time_layout.setContentsMargins(14, 12, 14, 12)
        time_layout.setSpacing(10)
        time_text = QVBoxLayout()
        self.time_label = QLabel()
        self.time_label.setObjectName("TimeLabel")
        self.date_label = QLabel()
        self.date_label.setObjectName("MutedText")
        time_text.addWidget(self.time_label)
        time_text.addWidget(self.date_label)
        wave = QLabel("LISTEN")
        wave.setObjectName("WaveOrb")
        wave.setAlignment(Qt.AlignCenter)
        wave.setFixedSize(58, 58)
        time_layout.addLayout(time_text, 1)
        time_layout.addWidget(wave)
        layout.addWidget(time_card)

        system_card = QFrame()
        system_card.setObjectName("UtilityCard")
        system_layout = QVBoxLayout(system_card)
        system_layout.setContentsMargins(14, 12, 14, 12)
        system_layout.setSpacing(10)
        system_title = QLabel("System Overview")
        system_title.setObjectName("CardTitle")
        metric_grid = QGridLayout()
        metric_grid.setHorizontalSpacing(8)
        self.cpu_metric = self._metric_chip("CPU", "--")
        self.ram_metric = self._metric_chip("RAM", "--")
        self.battery_metric = self._metric_chip("Battery", "--")
        metric_grid.addWidget(self.cpu_metric, 0, 0)
        metric_grid.addWidget(self.ram_metric, 0, 1)
        metric_grid.addWidget(self.battery_metric, 0, 2)
        storage_row = QHBoxLayout()
        storage_label = QLabel("Storage")
        storage_label.setObjectName("MutedText")
        self.storage_value = QLabel("--")
        self.storage_value.setObjectName("MutedText")
        storage_row.addWidget(storage_label)
        storage_row.addStretch(1)
        storage_row.addWidget(self.storage_value)
        self.storage_progress = QProgressBar()
        self.storage_progress.setObjectName("StorageProgress")
        self.storage_progress.setTextVisible(False)
        self.storage_progress.setRange(0, 100)
        system_layout.addWidget(system_title)
        system_layout.addLayout(metric_grid)
        system_layout.addLayout(storage_row)
        system_layout.addWidget(self.storage_progress)
        layout.addWidget(system_card)

        quick_card = QFrame()
        quick_card.setObjectName("UtilityCard")
        quick_layout = QVBoxLayout(quick_card)
        quick_layout.setContentsMargins(14, 12, 14, 12)
        quick_layout.setSpacing(10)
        quick_title = QLabel("Quick Actions")
        quick_title.setObjectName("CardTitle")
        quick_grid = QGridLayout()
        quick_grid.setSpacing(10)
        actions = [
            ("Calculator", "Calculator"),
            ("Notepad", "TextEdit"),
            ("Screenshot", "Screenshot"),
            ("Finder", "Finder"),
        ]
        for idx, (label, app_name) in enumerate(actions):
            button = QPushButton(label)
            button.setObjectName("QuickActionButton")
            button.clicked.connect(lambda _checked=False, name=app_name: self._quick_open_app(name))
            quick_grid.addWidget(button, idx // 2, idx % 2)
        quick_layout.addWidget(quick_title)
        quick_layout.addLayout(quick_grid)
        layout.addWidget(quick_card)

        notes_card = QFrame()
        notes_card.setObjectName("UtilityCard")
        notes_layout = QVBoxLayout(notes_card)
        notes_layout.setContentsMargins(14, 12, 14, 12)
        notes_layout.setSpacing(8)
        notes_title = QLabel("Recent Notes")
        notes_title.setObjectName("CardTitle")
        self.recent_notes_label = QLabel("No notes yet.")
        self.recent_notes_label.setObjectName("RecentNotesText")
        self.recent_notes_label.setWordWrap(True)
        new_note_button = QPushButton("+ New Note")
        new_note_button.setObjectName("SecondaryButton")
        new_note_button.clicked.connect(lambda: self._show_section(2))
        notes_layout.addWidget(notes_title)
        notes_layout.addWidget(self.recent_notes_label, 1)
        notes_layout.addWidget(new_note_button)
        layout.addWidget(notes_card, 1)

        return rail

    def _metric_chip(self, label: str, value: str) -> QLabel:
        chip = QLabel(f"{label}\n{value}")
        chip.setObjectName("MetricChip")
        chip.setAlignment(Qt.AlignCenter)
        chip.setMinimumHeight(62)
        return chip

    def _build_section(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        widget = QWidget()
        widget.setObjectName("Section")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(28, 22, 18, 16)
        layout.setSpacing(16)
        header = QFrame()
        header.setObjectName("SectionHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)
        label = QLabel(title)
        label.setObjectName("SectionTitle")
        chip = QLabel(f"{self.settings.ai_provider} / {self.settings.model}")
        chip.setObjectName("HeaderChip")
        chip.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(label)
        header_layout.addStretch(1)
        header_layout.addWidget(chip)
        layout.addWidget(header)
        return widget, layout

    def _build_chat_view(self) -> QWidget:
        widget, layout = self._build_section("Chat")

        self.chat_scroll = QScrollArea()
        self.chat_scroll.setObjectName("ChatScroll")
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setFrameShape(QFrame.NoFrame)
        self._add_shadow(self.chat_scroll, blur=26, color="#03121c", y=10)
        self.chat_messages = QWidget()
        self.chat_messages.setObjectName("ChatMessages")
        self.chat_messages_layout = QVBoxLayout(self.chat_messages)
        self.chat_messages_layout.setContentsMargins(18, 18, 18, 18)
        self.chat_messages_layout.setSpacing(14)
        self.chat_messages_layout.addStretch(1)
        self.chat_scroll.setWidget(self.chat_messages)
        layout.addWidget(self.chat_scroll, 1)

        entry_row = QHBoxLayout()
        entry_row.setSpacing(10)
        input_frame = QFrame()
        input_frame.setObjectName("ChatInputBar")
        self._add_shadow(input_frame, blur=28, color="#053747", y=8)
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(14, 8, 8, 8)
        input_layout.setSpacing(10)
        self.chat_entry = QLineEdit()
        self.chat_entry.setObjectName("ChatEntry")
        self.chat_entry.setPlaceholderText("Ask Jarvix...")
        self.chat_entry.returnPressed.connect(self._send_chat)
        send_button = QPushButton("Send")
        send_button.setObjectName("PrimaryButton")
        send_button.clicked.connect(self._send_chat)
        speak_button = QPushButton("Speak Last")
        speak_button.setObjectName("IconButton")
        speak_button.clicked.connect(self._speak_last_message)
        input_layout.addWidget(self.chat_entry, 1)
        input_layout.addWidget(send_button)
        input_layout.addWidget(speak_button)
        entry_row.addWidget(input_frame, 1)
        layout.addLayout(entry_row)
        return widget

    def _build_voice_view(self) -> QWidget:
        widget, layout = self._build_section("Voice")
        voice_panel = QFrame()
        voice_panel.setObjectName("HeroPanel")
        self._add_shadow(voice_panel, blur=30, color="#052838", y=10)
        voice_layout = QVBoxLayout(voice_panel)
        voice_layout.setContentsMargins(24, 34, 24, 28)
        voice_layout.setSpacing(14)
        voice_layout.setAlignment(Qt.AlignCenter)
        mic_ring = QLabel("MIC")
        mic_ring.setObjectName("MicRing")
        mic_ring.setAlignment(Qt.AlignCenter)
        mic_ring.setFixedSize(132, 132)
        voice_title = QLabel("Listening Console")
        voice_title.setObjectName("PanelHeading")
        voice_title.setAlignment(Qt.AlignCenter)
        voice_caption = QLabel("Push to talk. Speech is sent into Chat.")
        voice_caption.setObjectName("MutedText")
        voice_caption.setAlignment(Qt.AlignCenter)
        voice_layout.addWidget(mic_ring, alignment=Qt.AlignCenter)
        voice_layout.addWidget(voice_title)
        voice_layout.addWidget(voice_caption)
        layout.addWidget(voice_panel)

        self.voice_output = QTextEdit()
        self.voice_output.setReadOnly(True)
        self.voice_output.setPlaceholderText("Push to talk transcription appears here.")
        layout.addWidget(self.voice_output, 1)
        record_button = QPushButton("Push to Talk")
        record_button.setObjectName("PrimaryButton")
        record_button.clicked.connect(self._record_voice)
        layout.addWidget(record_button)
        return widget

    def _build_notes_view(self) -> QWidget:
        widget, layout = self._build_section("Notes")
        self.note_title = QLineEdit()
        self.note_title.setPlaceholderText("Note title")
        self.note_body = QTextEdit()
        self.note_body.setPlaceholderText("Write a local note...")
        save_button = QPushButton("Create Note")
        save_button.setObjectName("PrimaryButton")
        save_button.clicked.connect(self._create_note)
        self.note_search = QLineEdit()
        self.note_search.setPlaceholderText("Search notes")
        self.note_search.textChanged.connect(self._search_notes)
        self.note_list = QListWidget()
        layout.addWidget(self.note_title)
        layout.addWidget(self.note_body, 1)
        layout.addWidget(save_button)
        layout.addWidget(self.note_search)
        layout.addWidget(self.note_list, 1)
        return widget

    def _build_apps_view(self) -> QWidget:
        widget, layout = self._build_section("Apps")
        self.app_name = QLineEdit()
        self.app_name.setPlaceholderText("macOS app name, e.g. Calculator")
        open_app_button = QPushButton("Open App")
        open_app_button.setObjectName("PrimaryButton")
        open_app_button.clicked.connect(self._confirm_open_app)
        self.website_url = QLineEdit()
        self.website_url.setPlaceholderText("https://example.com")
        open_site_button = QPushButton("Open Website")
        open_site_button.setObjectName("SecondaryButton")
        open_site_button.clicked.connect(self._confirm_open_website)
        layout.addWidget(self.app_name)
        layout.addWidget(open_app_button)
        layout.addSpacing(12)
        layout.addWidget(self.website_url)
        layout.addWidget(open_site_button)
        layout.addStretch(1)
        return widget

    def _build_system_view(self) -> QWidget:
        widget, layout = self._build_section("System")
        self.system_output = QTextEdit()
        self.system_output.setObjectName("SystemOutput")
        self.system_output.setReadOnly(True)
        refresh_button = QPushButton("Refresh System Info")
        refresh_button.setObjectName("SecondaryButton")
        refresh_button.clicked.connect(self._refresh_system_info)
        self.command_entry = QLineEdit()
        self.command_entry.setPlaceholderText("Safe command, e.g. pwd, ls -la, date")
        run_button = QPushButton("Validate and Run")
        run_button.setObjectName("PrimaryButton")
        run_button.clicked.connect(self._confirm_run_command)
        self.command_output = QTextEdit()
        self.command_output.setReadOnly(True)
        layout.addWidget(self.system_output)
        layout.addWidget(refresh_button)
        layout.addSpacing(10)
        layout.addWidget(self.command_entry)
        layout.addWidget(run_button)
        layout.addWidget(self.command_output, 1)
        return widget

    def _build_settings_view(self) -> QWidget:
        widget, layout = self._build_section("Settings")
        self.settings_output = QTextEdit()
        self.settings_output.setObjectName("SettingsOutput")
        self.settings_output.setReadOnly(True)
        self.tts_checkbox = QCheckBox("Enable text-to-speech")
        self.tts_checkbox.setChecked(self.settings.tts_enabled)
        self.tts_checkbox.toggled.connect(self.speech_service.set_enabled)
        self.speech_service.set_enabled(self.settings.tts_enabled)
        layout.addWidget(self.settings_output)
        layout.addWidget(self.tts_checkbox)
        layout.addStretch(1)
        openai_key_state = "configured" if self.settings.has_openai_key else "missing"
        gemini_key_state = "configured" if self.settings.has_gemini_key else "missing"
        active_key_state = "configured" if self.settings.has_ai_key else "missing"
        self.settings_output.setPlainText(
            "\n".join(
                [line for line in [
                    f"AI provider: {self.settings.ai_provider}",
                    f"Active provider key: {active_key_state}",
                    (
                        f"Key warning: {self.settings.active_key_warning}"
                        if self.settings.active_key_warning
                        else ""
                    ),
                    f"OpenAI API key: {openai_key_state}",
                    f"Gemini API key: {gemini_key_state}",
                    f"Model: {self.settings.model}",
                    f"Database: {self.settings.database_path}",
                    "",
                    "Jarvix stores notes locally and reads AI credentials from environment variables only.",
                ] if line]
            )
        )
        return widget

    def _show_section(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for button_index, button in enumerate(self.nav_buttons):
            button.setProperty("active", button_index == index)
            button.style().unpolish(button)
            button.style().polish(button)

    def _set_status(self, state: str) -> None:
        self.status_label.setText(f"status: {state}")
        colors = {
            "idle": "#2d7488",
            "listening": "#35d0ff",
            "thinking": "#b28cff",
            "speaking": "#7dffb2",
            "error": "#ff6b7a",
        }
        color = colors.get(state, "#2d7488")
        self.status_label.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            "stop:0 rgba(20, 184, 166, 70), stop:1 rgba(11, 17, 23, 210)); "
            f"border-color: {color}; color: #ecfbff;"
        )
        animation = QPropertyAnimation(self.status_label, b"windowOpacity", self)
        animation.setDuration(180)
        animation.setStartValue(0.72)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.start()
        self._status_animation = animation

    def _send_chat(self) -> None:
        text = self.chat_entry.text().strip()
        if not text:
            return
        self.chat_entry.clear()
        self._submit_chat_text(text)

    def _submit_chat_text(self, text: str) -> None:
        self.messages.append(ChatMessage("user", text))
        self._append_chat("You", text)
        action = self.chat_actions.plan(text)
        if action is not None:
            self._handle_chat_action(action)
            return
        self._set_status("thinking")
        worker = FunctionWorker(self.ai_client.generate_reply, list(self.messages))
        worker.signals.finished.connect(self._handle_ai_reply)
        worker.signals.failed.connect(self._handle_ai_error)
        self.thread_pool.start(worker)

    def _handle_chat_action(self, action: ChatAction) -> None:
        if action.kind in {"calculation", "calculation_error", "launch_error"}:
            self._reply_locally(action.response, error=action.kind.endswith("error"))
            return

        if action.confirmation and not self._confirm(action.confirmation):
            self._reply_locally("Cancelled.")
            return

        try:
            if action.kind == "open_website":
                result = self.launcher.open_website(action.target)
            elif action.kind == "open_app":
                result = self.launcher.open_app(action.target)
            else:
                self._reply_locally("I understood that as a local action, but v1 cannot run it yet.")
                return
        except LauncherError as exc:
            self._reply_locally(f"I couldn't open that: {exc}", error=True)
            return

        self._reply_locally(result.message)

    def _reply_locally(self, text: str, error: bool = False) -> None:
        self.messages.append(ChatMessage("assistant", text))
        self._append_chat("Jarvix", text)
        self._set_status("error" if error else "idle")
        if not error and self.speech_service.enabled:
            self._set_status("speaking")
            self.speech_service.speak(text)
            self._set_status("idle")

    def _handle_ai_reply(self, reply: object) -> None:
        text = str(reply)
        self.messages.append(ChatMessage("assistant", text))
        self._append_chat("Jarvix", text)
        self._set_status("idle")
        if self.speech_service.enabled:
            self._set_status("speaking")
            self.speech_service.speak(text)
            self._set_status("idle")

    def _handle_ai_error(self, message: str) -> None:
        self._append_chat("Jarvix", message)
        self._set_status("error")

    def _append_chat(self, speaker: str, text: str) -> None:
        self.chat_transcript.append(f"{speaker}: {text}")
        is_user = speaker.lower() == "you"
        row = QWidget()
        row.setObjectName("MessageRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(10)

        bubble = QFrame()
        bubble.setObjectName("UserBubble" if is_user else "AssistantBubble")
        bubble.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)
        bubble.setMaximumWidth(560)
        self._add_shadow(
            bubble,
            blur=24 if is_user else 28,
            color="#06283a" if is_user else "#02070d",
            y=8,
        )
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(14, 12, 14, 10)
        bubble_layout.setSpacing(6)

        role = QLabel(speaker)
        role.setObjectName("BubbleRole")
        role.setTextFormat(Qt.PlainText)
        content = QLabel(text)
        content.setObjectName("BubbleText")
        content.setTextFormat(Qt.PlainText)
        content.setWordWrap(True)
        timestamp = QLabel(datetime.now().strftime("%I:%M %p").lstrip("0"))
        timestamp.setObjectName("BubbleTime")
        timestamp.setAlignment(Qt.AlignRight)
        bubble_layout.addWidget(role)
        bubble_layout.addWidget(content)
        bubble_layout.addWidget(timestamp)

        if is_user:
            row_layout.addStretch(1)
            row_layout.addWidget(bubble)
        else:
            avatar = QLabel("JX")
            avatar.setObjectName("BotAvatar")
            avatar.setAlignment(Qt.AlignCenter)
            avatar.setFixedSize(42, 42)
            row_layout.addWidget(avatar, alignment=Qt.AlignTop)
            row_layout.addWidget(bubble)
            row_layout.addStretch(1)

        insert_at = max(0, self.chat_messages_layout.count() - 1)
        self.chat_messages_layout.insertWidget(insert_at, row)
        QTimer.singleShot(0, self._scroll_chat_to_bottom)

    def _chat_plain_text(self) -> str:
        return "\n".join(self.chat_transcript)

    def _scroll_chat_to_bottom(self) -> None:
        scroll_bar = self.chat_scroll.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

    def _add_shadow(
        self,
        widget: QWidget,
        blur: int = 24,
        color: str = "#000000",
        x: int = 0,
        y: int = 8,
    ) -> None:
        shadow = QGraphicsDropShadowEffect(widget)
        shadow.setBlurRadius(blur)
        shadow.setOffset(x, y)
        shadow.setColor(QColor(color))
        widget.setGraphicsEffect(shadow)

    def _speak_last_message(self) -> None:
        for message in reversed(self.messages):
            if message.role == "assistant":
                self._set_status("speaking")
                self.speech_service.speak(message.content)
                self._set_status("idle")
                return
        self._show_info("No assistant message to speak yet.")

    def _record_voice(self) -> None:
        self._set_status("listening")
        worker = FunctionWorker(self.transcriber.transcribe_once)
        worker.signals.finished.connect(self._handle_voice_text)
        worker.signals.failed.connect(self._handle_voice_error)
        self.thread_pool.start(worker)

    def _handle_voice_text(self, text: object) -> None:
        self._set_status("idle")
        value = str(text)
        self.voice_output.append(f"Heard: {value}")
        self._show_section(0)
        self._submit_chat_text(value)

    def _handle_voice_error(self, message: str) -> None:
        self._set_status("error")
        self.voice_output.append(f"Voice error: {message}")

    def _create_note(self) -> None:
        note = self.notes_store.create_note(self.note_title.text(), self.note_body.toPlainText())
        self.note_title.clear()
        self.note_body.clear()
        self._refresh_notes()
        self._show_info(f"Created note: {note.title}")

    def _search_notes(self) -> None:
        self._populate_notes(self.notes_store.search_notes(self.note_search.text()))

    def _refresh_notes(self) -> None:
        if hasattr(self, "note_list"):
            recent_notes = self.notes_store.list_recent()
            self._populate_notes(recent_notes)
            self._populate_recent_notes_preview(recent_notes)

    def _populate_recent_notes_preview(self, notes: object) -> None:
        if not hasattr(self, "recent_notes_label"):
            return
        lines = []
        for note in list(notes)[:3]:
            body = note.body[:44] + ("..." if len(note.body) > 44 else "")
            lines.append(f"{note.title}\n{body or 'No body'}")
        self.recent_notes_label.setText("\n\n".join(lines) if lines else "No notes yet.")

    def _populate_notes(self, notes: object) -> None:
        self.note_list.clear()
        for note in notes:
            item = QListWidgetItem(f"{note.title}\n{note.body[:140]}")
            item.setData(Qt.UserRole, note.id)
            self.note_list.addItem(item)

    def _confirm_open_app(self) -> None:
        app_name = self.app_name.text().strip()
        if self._confirm(f"Open app `{app_name}`?"):
            try:
                result = self.launcher.open_app(app_name)
            except LauncherError as exc:
                self._show_error(str(exc))
            else:
                self._show_info(result.message)

    def _confirm_open_website(self) -> None:
        url = self.website_url.text().strip()
        try:
            normalized = self.launcher.normalize_url(url)
        except LauncherError as exc:
            self._show_error(str(exc))
            return
        if self._confirm(f"Open website `{normalized}`?"):
            try:
                result = self.launcher.open_website(normalized)
            except LauncherError as exc:
                self._show_error(str(exc))
            else:
                self._show_info(result.message)

    def _refresh_system_info(self) -> None:
        if not hasattr(self, "system_output"):
            return
        try:
            snapshot = self.system_info.snapshot()
        except RuntimeError as exc:
            self.system_output.setPlainText(str(exc))
            return
        values = asdict(snapshot)
        lines = [
            f"CPU: {values['cpu_percent']}%",
            (
                f"RAM: {values['ram_percent']}% "
                f"({values['ram_used_gb']} GB / {values['ram_total_gb']} GB)"
            ),
            (
                "Battery: unavailable"
                if values["battery_percent"] is None
                else f"Battery: {values['battery_percent']}% plugged={values['battery_plugged']}"
            ),
            (
                f"Storage: {values['storage_percent']}% "
                f"({values['storage_used_gb']} GB / {values['storage_total_gb']} GB)"
            ),
        ]
        self.system_output.setPlainText("\n".join(lines))
        if hasattr(self, "cpu_metric"):
            self.cpu_metric.setText(f"CPU\n{int(values['cpu_percent'])}%")
            self.ram_metric.setText(f"RAM\n{int(values['ram_percent'])}%")
            battery_text = (
                "--"
                if values["battery_percent"] is None
                else f"{int(values['battery_percent'])}%"
            )
            self.battery_metric.setText(f"Battery\n{battery_text}")
            self.storage_value.setText(
                f"{values['storage_used_gb']} GB / {values['storage_total_gb']} GB"
            )
            self.storage_progress.setValue(int(values["storage_percent"]))

    def _refresh_top_info(self) -> None:
        if not hasattr(self, "time_label"):
            return
        now = datetime.now()
        self.time_label.setText(now.strftime("%I:%M %p").lstrip("0"))
        self.date_label.setText(f"{now.strftime('%A, %B')} {now.day}, {now.year}")

    def _quick_open_app(self, app_name: str) -> None:
        if not self._confirm(f"Open app `{app_name}`?"):
            return
        try:
            result = self.launcher.open_app(app_name)
        except LauncherError as exc:
            self._show_error(str(exc))
            return
        self._reply_locally(result.message)

    def _confirm_run_command(self) -> None:
        command = self.command_entry.text().strip()
        validation = self.command_runner.safety.validate(command)
        if not validation.allowed:
            self.command_output.setPlainText(f"Blocked: {validation.reason}")
            self._set_status("error")
            return
        if not self._confirm(f"Run safe command `{command}`?"):
            return
        try:
            result = self.command_runner.run(command, confirmed=True)
        except Exception as exc:
            self.command_output.setPlainText(str(exc))
            self._set_status("error")
            return
        output = result.stdout.strip() or result.stderr.strip() or "(no output)"
        self.command_output.setPlainText(output)
        self._set_status("idle")

    def _confirm(self, text: str) -> bool:
        return (
            QMessageBox.question(self, "Jarvix confirmation", text)
            == QMessageBox.StandardButton.Yes
        )

    def _show_info(self, text: str) -> None:
        QMessageBox.information(self, "Jarvix", text)

    def _show_error(self, text: str) -> None:
        QMessageBox.warning(self, "Jarvix", text)
        self._set_status("error")
