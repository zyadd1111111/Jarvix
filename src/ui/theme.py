from __future__ import annotations


APP_STYLESHEET = """
QWidget#Root {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #050A0F, stop:0.42 #08131D, stop:1 #02060A);
}
QWidget {
    background: transparent;
    color: #E5E7EB;
    font-family: "Poppins", "Inter", "SF Pro Display", Arial;
    font-size: 14px;
}
QMainWindow {
    background: #050A0F;
}
QFrame#Sidebar,
QFrame#RightRail,
QStackedWidget#ContentPanel {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(15, 22, 32, 232),
        stop:0.48 rgba(8, 19, 29, 218),
        stop:1 rgba(4, 10, 16, 238));
    border: 1px solid rgba(96, 165, 250, 42);
}
QFrame#Sidebar {
    border-top-left-radius: 18px;
    border-bottom-left-radius: 18px;
    border-right: 1px solid rgba(34, 211, 238, 62);
}
QStackedWidget#ContentPanel {
    border-left: 0;
    border-right: 0;
}
QFrame#RightRail {
    border-top-right-radius: 18px;
    border-bottom-right-radius: 18px;
    border-left: 1px solid rgba(34, 211, 238, 54);
}
QFrame#SectionHeader {
    background: transparent;
    border: 0;
}
QLabel#Brand {
    color: #22D3EE;
    font-size: 31px;
    font-weight: 900;
    letter-spacing: 0;
}
QLabel#SidebarSubtitle,
QLabel#MutedText,
QLabel#BubbleTime {
    color: #9AAFC3;
    font-size: 12px;
}
QLabel#Caption {
    color: #D7E8F4;
    font-size: 12px;
    font-weight: 800;
}
QLabel#OnlineText {
    color: #34D399;
    font-weight: 800;
}
QLabel#SectionTitle {
    color: #F8FAFC;
    font-size: 19px;
    font-weight: 800;
}
QLabel#HeaderChip {
    background: rgba(20, 184, 166, 34);
    border: 1px solid rgba(34, 211, 238, 70);
    border-radius: 11px;
    color: #A7F3D0;
    font-size: 12px;
    padding: 5px 10px;
}
QLabel#PanelHeading {
    color: #F8FAFC;
    font-size: 21px;
    font-weight: 800;
}
QLabel#StatusPill {
    background: rgba(20, 184, 166, 36);
    border: 1px solid #148BA6;
    border-radius: 13px;
    color: #D9FBFF;
    padding: 6px 11px;
}
QLabel#ProfileAvatar {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(96, 165, 250, 128), stop:1 rgba(15, 22, 32, 235));
    border: 1px solid rgba(229, 231, 235, 52);
    border-radius: 19px;
    color: #F8FAFC;
    font-size: 18px;
    font-weight: 900;
}
QFrame#SidebarCard,
QFrame#ProfileCard,
QFrame#HeroPanel,
QFrame#UtilityCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(22, 37, 52, 180),
        stop:0.52 rgba(11, 24, 36, 160),
        stop:1 rgba(5, 12, 20, 210));
    border: 1px solid rgba(96, 165, 250, 42);
    border-radius: 12px;
}
QFrame#HeroPanel {
    border-color: rgba(34, 211, 238, 76);
}
QLabel#TimeLabel {
    color: #F8FAFC;
    font-size: 18px;
    font-weight: 900;
}
QLabel#WaveOrb {
    background: rgba(5, 18, 27, 190);
    border: 2px solid rgba(20, 184, 166, 92);
    border-radius: 29px;
    color: #22D3EE;
    font-size: 9px;
    font-weight: 900;
}
QLabel#CardTitle {
    color: #F8FAFC;
    font-size: 13px;
    font-weight: 900;
}
QLabel#MetricChip {
    background: rgba(7, 18, 27, 168);
    border: 1px solid rgba(34, 211, 238, 48);
    border-radius: 31px;
    color: #E5E7EB;
    font-size: 12px;
    font-weight: 800;
    padding: 7px;
}
QLabel#RecentNotesText {
    color: #B7C8D8;
    font-size: 11px;
    line-height: 135%;
}
QPushButton {
    background: rgba(8, 19, 29, 164);
    border: 1px solid rgba(96, 165, 250, 44);
    border-radius: 10px;
    color: #E5E7EB;
    padding: 10px 14px;
}
QPushButton:hover {
    background: rgba(20, 184, 166, 42);
    border-color: rgba(34, 211, 238, 132);
    color: #FFFFFF;
}
QPushButton:pressed {
    background: rgba(20, 184, 166, 82);
}
QPushButton#PrimaryButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #148BA6, stop:0.55 #14B8A6, stop:1 #0E7490);
    border-color: rgba(34, 211, 238, 160);
    color: #FFFFFF;
    font-weight: 800;
}
QPushButton#PrimaryButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #22D3EE, stop:1 #14B8A6);
}
QPushButton#SecondaryButton,
QPushButton#IconButton,
QPushButton#QuickActionButton {
    background: rgba(7, 18, 27, 152);
    border-color: rgba(96, 165, 250, 44);
}
QPushButton#QuickActionButton {
    min-height: 46px;
    font-size: 12px;
    padding: 8px;
}
QPushButton[nav="true"] {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    color: #E5E7EB;
    padding: 12px 12px;
    text-align: left;
}
QPushButton[nav="true"]:hover {
    background: rgba(20, 184, 166, 28);
    border-color: rgba(34, 211, 238, 64);
}
QPushButton[active="true"] {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(20, 184, 166, 135), stop:1 rgba(8, 19, 29, 112));
    border-color: rgba(34, 211, 238, 150);
    color: #FFFFFF;
}
QLineEdit,
QTextEdit,
QListWidget {
    background: rgba(7, 18, 27, 166);
    border: 1px solid rgba(96, 165, 250, 44);
    border-radius: 10px;
    color: #E5E7EB;
    padding: 10px;
    selection-background-color: #148BA6;
}
QLineEdit:focus,
QTextEdit:focus {
    border-color: rgba(34, 211, 238, 148);
    background: rgba(7, 18, 27, 212);
}
QLineEdit#ChatEntry {
    background: transparent;
    border: 0;
    padding: 9px 3px;
    font-size: 15px;
}
QFrame#ChatInputBar {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(8, 19, 29, 218), stop:1 rgba(13, 36, 49, 198));
    border: 1px solid rgba(34, 211, 238, 118);
    border-radius: 15px;
}
QScrollArea#ChatScroll {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(4, 13, 20, 194),
        stop:0.52 rgba(6, 20, 30, 178),
        stop:1 rgba(4, 10, 16, 224));
    border: 1px solid rgba(96, 165, 250, 36);
    border-radius: 14px;
}
QWidget#ChatMessages {
    background: transparent;
}
QFrame#AssistantBubble,
QFrame#UserBubble {
    border-radius: 14px;
    padding: 0;
}
QFrame#AssistantBubble {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(20, 31, 44, 224),
        stop:1 rgba(11, 19, 29, 238));
    border: 1px solid rgba(96, 165, 250, 44);
}
QFrame#UserBubble {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(20, 184, 166, 72),
        stop:1 rgba(13, 41, 54, 230));
    border: 1px solid rgba(34, 211, 238, 118);
}
QLabel#BubbleRole {
    color: #22D3EE;
    font-size: 12px;
    font-weight: 900;
}
QLabel#BubbleText {
    color: #F8FAFC;
    font-size: 14px;
    line-height: 145%;
}
QLabel#BotAvatar {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(20, 184, 166, 76), stop:1 rgba(5, 18, 27, 235));
    border: 1px solid rgba(34, 211, 238, 160);
    border-radius: 21px;
    color: #22D3EE;
    font-size: 9px;
    font-weight: 900;
}
QLabel#MicRing {
    background: rgba(6, 18, 27, 188);
    border: 2px solid rgba(20, 184, 166, 148);
    border-radius: 66px;
    color: #22D3EE;
    font-size: 24px;
    font-weight: 900;
}
QTextEdit#SystemOutput,
QTextEdit#SettingsOutput {
    background: rgba(14, 28, 40, 172);
}
QProgressBar#StorageProgress {
    background: rgba(5, 12, 20, 196);
    border: 1px solid rgba(96, 165, 250, 36);
    border-radius: 5px;
    height: 9px;
}
QProgressBar#StorageProgress::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #22D3EE, stop:1 #14B8A6);
    border-radius: 4px;
}
QCheckBox {
    spacing: 8px;
    color: #E5E7EB;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 4px;
}
QScrollBar::handle:vertical {
    background: rgba(96, 165, 250, 52);
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(34, 211, 238, 130);
}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0;
}
"""

