"""Colors + Qt stylesheet (dark, gaming-oriented)."""
C = {
    "bg": "#0d0e12", "bg2": "#13141a", "bg3": "#1a1c24", "bg4": "#22252f", "line": "#262935", "line2": "#323644",
    "text": "#eceef3", "text2": "#a8adbb", "text3": "#6f7484", "accent": "#f5b70a", "accent2": "#ffcf4a", "ink": "#1a1400",
    "red": "#ef4444", "green": "#22c55e", "blue": "#3b82f6", "purple": "#a78bfa",
}

QSS = f"""
* {{ font-family: 'Segoe UI Variable Text', 'Segoe UI'; font-size: 13px; color: {C['text']}; outline: 0; }}
QMainWindow, QWidget#page, QWidget#root {{ background: {C['bg']}; }}
QToolTip {{ background: {C['bg4']}; color: {C['text']}; border: 1px solid {C['line2']}; padding: 4px 8px; border-radius: 6px; }}
QLabel {{ background: transparent; }}
QLabel[muted="true"] {{ color: {C['text2']}; }}
QLabel[dim="true"] {{ color: {C['text3']}; font-size: 12px; }}
QLabel[h1="true"] {{ font-size: 17px; font-weight: 700; }}
QLabel[h2="true"] {{ font-size: 15px; font-weight: 700; }}
QLabel[h3="true"] {{ font-size: 11.5px; font-weight: 700; color: {C['text2']}; letter-spacing: 0.6px; }}
QWidget#sidebar {{ background: {C['bg2']}; border-right: 1px solid {C['line']}; }}
QWidget#topbar {{ border-bottom: 1px solid {C['line']}; }}
QFrame#card, QFrame#setcard {{ background: {C['bg2']}; border: 1px solid {C['line']}; border-radius: 14px; }}
QFrame#card[accent="true"] {{ border-color: {C['accent']}; }}
QFrame#hero {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2a2410, stop:0.35 {C['bg2']}, stop:1 {C['bg2']}); border: 1px solid {C['line']}; border-radius: 18px; }}
QFrame#inset {{ background: {C['bg3']}; border: 1px solid {C['line']}; border-radius: 12px; }}
QFrame#row {{ border-top: 1px solid {C['line']}; background: transparent; }}
QFrame#row[first="true"] {{ border-top: 0; }}
QPushButton {{ background: {C['bg3']}; border: 1px solid {C['line2']}; border-radius: 8px; padding: 0 14px; min-height: 32px; font-weight: 600; }}
QPushButton:hover {{ background: {C['bg4']}; border-color: #3c4152; }}
QPushButton:pressed {{ background: {C['bg3']}; }}
QPushButton:disabled {{ color: {C['text3']}; border-color: {C['line']}; }}
QPushButton[primary="true"] {{ background: {C['accent']}; border-color: {C['accent']}; color: {C['ink']}; }}
QPushButton[primary="true"]:hover {{ background: {C['accent2']}; }}
QPushButton[primary="true"]:disabled {{ background: #6b5a1f; color: #2a2410; }}
QPushButton[danger="true"] {{ color: #fca5a5; }}
QPushButton[danger="true"]:hover {{ background: rgba(239,68,68,0.14); border-color: rgba(239,68,68,0.4); }}
QPushButton[rec="true"] {{ background: {C['red']}; border-color: {C['red']}; color: white; }}
QPushButton[ghost="true"] {{ background: transparent; border-color: transparent; }}
QPushButton[ghost="true"]:hover {{ background: {C['bg3']}; }}
QPushButton[sm="true"] {{ min-height: 26px; padding: 0 10px; font-size: 12px; border-radius: 7px; }}
QPushButton[lg="true"] {{ min-height: 42px; padding: 0 20px; font-size: 14.5px; border-radius: 10px; }}
QPushButton[nav="true"] {{ background: transparent; border: 0; text-align: left; padding: 0 12px; min-height: 36px; color: {C['text2']}; font-weight: 600; border-radius: 8px; }}
QPushButton[nav="true"]:hover {{ background: {C['bg3']}; color: {C['text']}; }}
QPushButton[nav="true"]:checked {{ background: {C['bg4']}; color: {C['text']}; }}
QPushButton[seg="true"] {{ background: transparent; border: 0; border-radius: 7px; padding: 0 11px; min-height: 26px; color: {C['text2']}; font-size: 12.5px; }}
QPushButton[seg="true"]:checked {{ background: {C['bg4']}; color: {C['text']}; }}
QPushButton[seg="true"][accent="true"]:checked {{ background: {C['accent']}; color: {C['ink']}; }}
QFrame#segbox {{ background: {C['bg3']}; border: 1px solid {C['line2']}; border-radius: 9px; }}
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
  background: {C['bg3']}; border: 1px solid {C['line2']}; border-radius: 8px; padding: 5px 9px; min-height: 22px; selection-background-color: {C['accent']}; selection-color: {C['ink']};
}}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {C['accent']}; }}
QComboBox::drop-down {{ border: 0; width: 22px; }}
QComboBox QAbstractItemView {{ background: {C['bg3']}; border: 1px solid {C['line2']}; selection-background-color: {C['bg4']}; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: 0; }}
QSlider::groove:horizontal {{ height: 4px; background: {C['bg4']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {C['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QProgressBar {{ background: {C['bg4']}; border: 0; border-radius: 3px; max-height: 6px; min-height: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {C['accent']}; border-radius: 3px; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: 0; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2b2e3a; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #2b2e3a; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ width: 0; height: 0; background: none; }}
QListView {{ background: transparent; border: 0; }}
QListWidget {{ background: transparent; border: 0; }}
QListWidget::item {{ padding: 7px 10px; border-radius: 8px; color: {C['text2']}; }}
QListWidget::item:hover {{ background: {C['bg3']}; color: {C['text']}; }}
QListWidget::item:selected {{ background: {C['bg4']}; color: {C['text']}; }}
QMenu {{ background: {C['bg3']}; border: 1px solid {C['line2']}; padding: 5px; border-radius: 8px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {C['bg4']}; }}
QMenu::separator {{ height: 1px; background: {C['line']}; margin: 4px 6px; }}
QDialog {{ background: {C['bg2']}; }}
QTabBar::tab {{ background: transparent; padding: 8px 14px; color: {C['text2']}; border: 0; font-weight: 600; }}
QTabBar::tab:selected {{ color: {C['accent']}; border-bottom: 2px solid {C['accent']}; }}
QTabWidget::pane {{ border: 0; }}
QCheckBox {{ spacing: 8px; }}
"""
