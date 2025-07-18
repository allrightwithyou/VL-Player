import sys, os, cv2, random, json
import gc
from datetime import datetime, time, timedelta, date
from PyQt5.QtCore import Qt, QTimer, QRect, QTime, QEvent, QAbstractItemModel, QItemSelectionModel, QSize, QDate, QThread, pyqtSignal, QMimeData
from PyQt5.QtWidgets import (
    QApplication, QLabel, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QMessageBox, QListWidget, QFileDialog,
    QListWidgetItem, QLineEdit, QLabel as QLabelWidget,
    QInputDialog, QCheckBox, QAbstractItemView, QMenu,
    QComboBox, QStyledItemDelegate, QStyleOptionButton, QStyle,
    QTimeEdit, QDialog, QFormLayout, QDialogButtonBox,
    QSplitter, QFrame, QTextEdit, QTableWidget, QTableWidgetItem, QDateEdit, QSizePolicy, QToolButton
)
from PyQt5.QtGui import QPixmap, QImage, QStandardItemModel, QStandardItem, QCursor, QFont, QIcon, QColor
import csv
from PyQt5 import QtCore
from functools import partial

print('PyQt5 version:', QtCore.PYQT_VERSION_STR)

SUPPORTED_VIDEO_EXTS = ['.mp4', '.avi', '.mov', '.mkv']
SUPPORTED_IMAGE_EXTS = ['.jpg', '.jpeg', '.png', '.bmp', '.webp']
SUPPORTED_EXTS = SUPPORTED_VIDEO_EXTS + SUPPORTED_IMAGE_EXTS

DAYS_OF_WEEK = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']

SETTINGS_FILE = 'settings.json'

def is_valid_geometry(val):
    try:
        if type(val).__name__ == 'Never':
            return False
        return (
            val is not None and
            isinstance(val, (list, tuple)) and
            len(val) == 4 and
            all(isinstance(x, (int, float)) for x in val)
        )
    except Exception:
        return False

class GroupSchedule:
    def __init__(self, start_time=None, end_time=None, days=None, interval_minutes=None, is_interval_group=False):
        self.start_time = start_time
        self.end_time = end_time
        self.days = days or []
        self.interval_minutes = interval_minutes
        self.is_interval_group = is_interval_group
    def is_active_now(self):
        if not self.start_time or not self.end_time or not self.days:
            print("[GroupSchedule] is_active_now: Нет времени или дней, возвращаем True")
            return True
        now = datetime.now()
        current_day = now.weekday()
        if current_day not in self.days:
            print(f"[GroupSchedule] is_active_now: Сегодня ({DAYS_OF_WEEK[current_day]}) не в списке дней {self.days}")
            return False
        start_dt = datetime.combine(now.date(), self.start_time.toPyTime())
        end_dt = datetime.combine(now.date(), self.end_time.toPyTime())
        if start_dt <= end_dt:
            result = start_dt.time() <= now.time() <= end_dt.time()
            print(f"[GroupSchedule] is_active_now: Проверка {start_dt.time()} <= {now.time()} <= {end_dt.time()}: {result}")
            return result
        else:
            result = now.time() >= start_dt.time() or now.time() <= end_dt.time()
            print(f"[GroupSchedule] is_active_now: Ночное расписание, {now.time()} >= {start_dt.time()} или <= {end_dt.time()}: {result}")
            return result

class GroupScheduleDialog(QDialog):
    def __init__(self, parent=None, schedule=None):
        super().__init__(parent)
        self.setWindowTitle("Настройка расписания группы")
        self.setModal(True)
        layout = QFormLayout(self)
        self.start_time_edit = QTimeEdit(self)
        self.start_time_edit.setTime(QTime(9, 0) if not schedule or not schedule.start_time else schedule.start_time)
        layout.addRow("Время начала:", self.start_time_edit)
        self.end_time_edit = QTimeEdit(self)
        self.end_time_edit.setTime(QTime(18, 0) if not schedule or not schedule.end_time else schedule.end_time)
        layout.addRow("Время окончания:", self.end_time_edit)
        self.day_checkboxes = []
        days_layout = QVBoxLayout()
        for i, day in enumerate(DAYS_OF_WEEK):
            checkbox = QCheckBox(day)
            if schedule and schedule.days and i in schedule.days:
                checkbox.setChecked(True)
            self.day_checkboxes.append(checkbox)
            days_layout.addWidget(checkbox)
        days_widget = QWidget()
        days_widget.setLayout(days_layout)
        layout.addRow("Дни недели:", days_widget)
        self.interval_edit = QLineEdit(self)
        self.interval_edit.setPlaceholderText("Например, 60")
        if schedule and schedule.interval_minutes:
            self.interval_edit.setText(str(schedule.interval_minutes))
        layout.addRow("Интервал (мин):", self.interval_edit)
        self.interval_group_checkbox = QCheckBox("Интервальная группа (запуск только по интервалу)")
        if schedule and getattr(schedule, 'is_interval_group', False):
            self.interval_group_checkbox.setChecked(True)
        layout.addRow(self.interval_group_checkbox)
        def on_interval_group_toggled(checked):
            self.interval_edit.setEnabled(checked)
        self.interval_group_checkbox.toggled.connect(on_interval_group_toggled)
        self.interval_edit.setEnabled(self.interval_group_checkbox.isChecked())
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
    def get_schedule(self):
        selected_days = [i for i, cb in enumerate(self.day_checkboxes) if cb.isChecked()]
        try:
            interval = int(self.interval_edit.text())
            if interval <= 0:
                interval = None
        except Exception:
            interval = None
        return GroupSchedule(
            start_time=self.start_time_edit.time(),
            end_time=self.end_time_edit.time(),
            days=selected_days,
            interval_minutes=interval,
            is_interval_group=self.interval_group_checkbox.isChecked()
        )

class PlaylistItem:
    def __init__(self, path, duration=None, loops=1):
        self.path = path
        self.duration = duration
        self.loops = loops
        self._current_loop = 0
        self.groups = set()
    def reset(self):
        self._current_loop = 0

class VideoWindow(QWidget):
    def __init__(self, width, height):
        super().__init__()
        self.win_width = width
        self.win_height = height
        self.label = QLabel(self)
        self.label.setFixedSize(width, height)
        self.label.setStyleSheet("background-color:black;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.addWidget(self.label)
        frameless = getattr(Qt, 'FramelessWindowHint', None)
        stay_on_top = getattr(Qt, 'WindowStaysOnTopHint', None)
        if frameless is not None and stay_on_top is not None:
            self.setWindowFlags(frameless | stay_on_top)
        elif frameless is not None:
            self.setWindowFlags(frameless)
        elif stay_on_top is not None:
            self.setWindowFlags(stay_on_top)
        self.move(0,0)
        self.setCursor(QCursor(Qt.CursorShape.BlankCursor))
    def moveEvent(self, e):
        self.move(0,0)
    def resize_window(self, w, h):
        self.win_width = w
        self.win_height = h
        self.setFixedSize(w, h)
        self.label.setFixedSize(w, h)
    def show_frame(self, frame):
        h, w = frame.shape[:2]
        if w != self.win_width or h != self.win_height:
            f = cv2.resize(frame, (self.win_width, self.win_height))
        else:
            f = frame
        c = cv2.cvtColor(f, cv2.COLOR_BGR2RGB)
        img = QImage(c.data, c.shape[1], c.shape[0], QImage.Format_RGB888)
        p = QPixmap.fromImage(img).scaled(self.win_width, self.win_height,
                                         Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.label.setPixmap(p)
    def show_image(self, path):
        img = cv2.imread(path)
        if img is None:
            return
        self.show_frame(img)
    def closeEvent(self, event):
        from PyQt5.QtWidgets import QApplication
        QApplication.quit()
        event.accept()

class CheckBoxDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
        check_box_style_option = QStyleOptionButton()
        check_box_style_option.rect = self.get_check_box_rect(option)
        check_box_style_option.state = QStyle.StateFlag.State_Enabled | (
            QStyle.StateFlag.State_On if checked else QStyle.StateFlag.State_Off)
        style = QApplication.style()
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_CheckBox, check_box_style_option, painter)
        text_rect = option.rect
        text_rect.setLeft(check_box_style_option.rect.right() + 5)
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if painter is not None:
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
    def editorEvent(self, event, model, option, index):
        if event is not None and model is not None and index is not None:
            key_space = getattr(QtCore.Qt, 'Key_Space', 0x20)
            if event.type() == QEvent.Type.MouseButtonRelease or (
                event.type() == QEvent.Type.KeyPress and getattr(event, 'key', None) == key_space):
                current = index.data(Qt.ItemDataRole.CheckStateRole)
                new_state = Qt.CheckState.Unchecked if current == Qt.CheckState.Checked else Qt.CheckState.Checked
                model.setData(index, new_state, Qt.ItemDataRole.CheckStateRole)
                return True
        return False
    def get_check_box_rect(self, option):
        check_box_style_option = QStyleOptionButton()
        style = QApplication.style()
        if style is not None:
            check_box_rect = style.subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, check_box_style_option, None)
        else:
            check_box_rect = QRect(0, 0, 16, 16)
        x = option.rect.x() + 5
        y = option.rect.y() + (option.rect.height() - check_box_rect.height()) // 2
        return QRect(x, y, check_box_rect.width(), check_box_rect.height())

def create_group_model(groups):
    model = QStandardItemModel()
    all_item = QStandardItem("Все")
    all_item.setFlags(all_item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
    all_item.setData(Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
    model.appendRow(all_item)
    for g in groups:
        item = QStandardItem(g)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        item.setData(Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
        model.appendRow(item)
    return model

def on_model_item_changed(changed_item):
    model = changed_item.model()
    model.blockSignals(True)
    try:
        if changed_item.text() == "Все":
            state = changed_item.checkState()
            for i in range(1, model.rowCount()):
                model.item(i).setCheckState(state)
        else:
            checked_count = sum(1 for i in range(1, model.rowCount()) if model.item(i).checkState() == Qt.CheckState.Checked)
            all_item = model.item(0)
            if checked_count == 0:
                all_item.setCheckState(Qt.CheckState.Checked)
            else:
                all_item.setCheckState(Qt.CheckState.Unchecked)
    finally:
        model.blockSignals(False)

class GroupManagerDialog(QDialog):
    def __init__(self, parent, groups, group_schedules, on_update):
        super().__init__(parent)
        self.setWindowTitle("Управление группами")
        self.groups = groups
        self.group_schedules = group_schedules
        self.on_update = on_update
        layout = QVBoxLayout(self)
        self.list = QListWidget(self)
        layout.addWidget(self.list)
        btns = QHBoxLayout()
        self.btn_add = QPushButton("Добавить группу")
        self.btn_del = QPushButton("Удалить группу")
        self.btn_edit = QPushButton("Редактировать расписание")
        self.btn_rename = QPushButton("Переименовать группу")
        btns.addWidget(self.btn_add)
        btns.addWidget(self.btn_del)
        btns.addWidget(self.btn_edit)
        btns.addWidget(self.btn_rename)
        layout.addLayout(btns)
        self.btn_add.clicked.connect(self.add_group)
        self.btn_del.clicked.connect(self.del_group)
        self.btn_edit.clicked.connect(self.edit_schedule)
        self.btn_rename.clicked.connect(self.rename_group)
        self.refresh()
    def refresh(self):
        self.list.clear()
        for g in self.groups:
            sch = self.group_schedules.get(g)
            interval = sch.interval_minutes if sch else None
            self.list.addItem(f"{g} | интервал: {interval or '-'} мин")
    def add_group(self):
        name, ok = QInputDialog.getText(self, "Новая группа", "Имя группы:")
        if ok and name.strip() and name.strip() not in self.groups:
            self.groups.append(name.strip())
            self.group_schedules[name.strip()] = None
            self.refresh()
            self.on_update()
            parent = self.parent()
            if isinstance(parent, Controller):
                parent.save_settings()
    def del_group(self):
        row = self.list.currentRow()
        if row >= 0:
            g = self.groups[row]
            del self.group_schedules[g]
            self.groups.pop(row)
            self.refresh()
            self.on_update()
            parent = self.parent()
            if isinstance(parent, Controller):
                parent.save_settings()
    def edit_schedule(self):
        row = self.list.currentRow()
        if row >= 0:
            g = self.groups[row]
            dialog = GroupScheduleDialog(self, self.group_schedules.get(g))
            if dialog.exec_() == QDialog.Accepted:
                self.group_schedules[g] = dialog.get_schedule()
                self.refresh()
                self.on_update()
                parent = self.parent()
                if isinstance(parent, Controller):
                    parent.save_settings()
    def rename_group(self):
        row = self.list.currentRow()
        if row >= 0:
            old_name = self.groups[row]
            new_name, ok = QInputDialog.getText(self, "Переименовать группу", f"Новое имя для группы '{old_name}':", text=old_name)
            new_name = new_name.strip()
            if ok and new_name and new_name != old_name and new_name not in self.groups:
                self.groups[row] = new_name
                self.group_schedules[new_name] = self.group_schedules.pop(old_name)
                parent = self.parent()
                if isinstance(parent, Controller):
                    for pi in parent.all_items:
                        if old_name in pi.groups:
                            pi.groups.remove(old_name)
                            pi.groups.add(new_name)
                    parent.save_settings()
                self.refresh()
                self.on_update()

class VideoThread(QThread):
    frame_ready = pyqtSignal(object)
    video_finished = pyqtSignal()
    def __init__(self, video_path, target_w, target_h, max_fps=24):
        super().__init__()
        self.video_path = video_path
        self.target_w = target_w
        self.target_h = target_h
        self.max_fps = None
        self._running = True
        self.frames_shown = 0
        print(f'[VideoThread] __init__: video_path={video_path}, target_w={target_w}, target_h={target_h}')
    def run(self):
        import cv2, time
        print(f'[VideoThread] run: starting video {self.video_path}')
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            print(f'[VideoThread] run: failed to open video {self.video_path}')
            self.video_finished.emit()
            return
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30
        interval = 1.0 / fps
        last_time = time.time()
        self.frames_shown = 0
        while self._running and cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                print(f'[VideoThread] run: end of video or read error for {self.video_path}')
                break
            self.frames_shown += 1
            self.frame_ready.emit(frame)
            elapsed = time.time() - last_time
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
            last_time = time.time()
        cap.release()
        print(f'[VideoThread] run: finished video {self.video_path}, frames shown: {self.frames_shown}')
        self.video_finished.emit()
    def stop(self):
        print(f'[VideoThread] stop: stopping video {self.video_path}')
        self._running = False
        self.wait()

class MiniControllerWindow(QDialog):
    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.setWindowTitle('Мини-контроллер')
        self.setFixedSize(340, 80)
        layout = QVBoxLayout(self)
        btn_layout = QHBoxLayout()
        self.btn_start = QPushButton('Старт')
        self.btn_start.setMinimumHeight(36)
        self.btn_start.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.btn_next = QPushButton('Следующий')
        self.btn_next.setMinimumHeight(36)
        self.btn_next.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.btn_stop = QPushButton('Стоп')
        self.btn_stop.setMinimumHeight(36)
        self.btn_stop.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_next)
        btn_layout.addWidget(self.btn_stop)
        layout.addLayout(btn_layout)
        def manual_next():
            self.controller.manual_skip = True
            self.controller.next_file()
        self.btn_next.clicked.connect(manual_next)
        self.btn_stop.clicked.connect(self.controller.stop_playlist)
        self.btn_start.clicked.connect(self.controller.start_playlist)
    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self.controller, 'mini_ctrl_geometry'):
            self.controller.mini_ctrl_geometry = list(self.geometry().getRect())
            self.controller.save_settings()
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self.controller, 'mini_ctrl_geometry'):
            self.controller.mini_ctrl_geometry = list(self.geometry().getRect())
            self.controller.save_settings()

class Controller(QWidget):
    def __init__(self, w=864, h=432):
        super().__init__()
        self.win_width = w
        self.win_height = h
        self.last_folder = ''
        self.groups = []
        self.group_schedules = {}
        self.all_items = []
        self.filter_groups = set()
        self.play_idx = -1
        self.ord = []
        self.cap = None
        self._current_playing = None
        self._mini_ctrl_win = None
        self.filter_file_text = ''
        self.filter_group_text = ''
        self.filter_start_date = None
        self.filter_end_date = None
        self.main_window_geometry = None
        self.mini_ctrl_geometry = None
        self._log_file = "logs.txt"
        self._current_log = None
        self.video_win = VideoWindow(w, h)
        self.video_win.show()
        self.pending_interval_groups = []
        self._last_active_groups_filter = set()
        self._group_view_mode = None
        self.manual_skip = False
        self._interval_playing_groups = set()  # множество проигрываемых интервальных групп
        self.last_interval_run = {}
        self.interval_check_timer = QTimer(self)
        self.interval_check_timer.timeout.connect(self.check_interval_groups)
        self.interval_check_timer.start(60_000)
        self.video_thread = None
        self.is_stopped = False
        self._just_manual = False
        self.group_timers = {}
        self._interval_group_playing = set()  # <--- добавлено для отслеживания активных интервальных групп
        main_layout = QVBoxLayout(self)
        top_layout = QHBoxLayout()
        self.btn_group_manager = QPushButton("Управление группами")
        self.btn_group_manager.setIconSize(QSize(28, 28))
        self.btn_group_manager.setMinimumHeight(40)
        self.btn_group_manager.setToolTip("Управление группами и расписаниями")
        top_layout.addWidget(self.btn_group_manager)
        self.btn_resize = QPushButton("Изменить размер плеера")
        self.btn_resize.setIconSize(QSize(28, 28))
        self.btn_resize.setMinimumHeight(40)
        self.btn_resize.setToolTip("Изменить размер окна плеера")
        top_layout.addWidget(self.btn_resize)
        self.btn_open = QPushButton("Открыть папку")
        self.btn_open.setIconSize(QSize(28, 28))
        self.btn_open.setMinimumHeight(40)
        self.btn_open.setToolTip("Открыть папку с файлами и добавить их в общий список")
        self.btn_open.clicked.connect(self.open_folder)
        self.btn_add_file = QPushButton("Добавить файл в группу")
        self.btn_add_file.setIconSize(QSize(28, 28))
        self.btn_add_file.setMinimumHeight(40)
        self.btn_add_file.setToolTip("Добавить файл в одну из групп")
        self.btn_add_file.clicked.connect(self.add_file_to_group)
        top_layout.addWidget(self.btn_add_file)
        self.btn_more = QToolButton()
        self.btn_more.setText('⋮')
        self.btn_more.setToolTip('Дополнительные действия')
        self.btn_more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self)
        self.action_logs = menu.addAction('Открыть логи', self.show_logs)
        self.action_mini_ctrl = menu.addAction('Мини-контроллер', self.open_mini_controller)
        self.action_reset = menu.addAction('Сбросить настройки', self.reset_settings)
        self.btn_more.setMenu(menu)
        top_layout.addWidget(self.btn_more)
        main_layout.addLayout(top_layout)
        splitter = QSplitter()
        splitter.setOrientation(Qt.Orientation.Horizontal)
        self.group_filter_list = QListWidget()
        self.group_filter_list.setMaximumWidth(300)
        splitter.addWidget(self.group_filter_list)
        self.file_list = PlaylistListWidget(self, self)
        self.file_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.file_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self.file_context_menu)
        splitter.addWidget(self.file_list)
        splitter.setSizes([200, 700])
        main_layout.addWidget(splitter)
        btn_layout = QHBoxLayout()
        self.btn_start = QPushButton("Старт")
        self.btn_start.setIconSize(QSize(28, 28))
        self.btn_start.setMinimumHeight(40)
        self.btn_start.setToolTip("Старт воспроизведения")
        self.btn_stop = QPushButton("Стоп")
        self.btn_stop.setIconSize(QSize(28, 28))
        self.btn_stop.setMinimumHeight(40)
        self.btn_stop.setToolTip("Остановить воспроизведение")
        self.btn_shuffle = QPushButton("Перемешать")
        self.btn_shuffle.setIcon(QIcon("shuffle.svg"))
        self.btn_shuffle.setIconSize(QSize(28, 28))
        self.btn_shuffle.setMinimumHeight(40)
        self.btn_shuffle.setToolTip("Перемешать плейлист")
        self.btn_repeat = QPushButton("Повтор")
        self.btn_repeat.setCheckable(True)
        self.btn_repeat.setChecked(True)
        self.btn_repeat.setIconSize(QSize(28, 28))
        self.btn_repeat.setMinimumHeight(40)
        self.btn_repeat.setToolTip("Режим повтора")
        pressed_style = """
        QPushButton:checked {
            background-color: #a0e0a0;
            border: 2px solid #388e3c;
        }
        """
        self.btn_repeat.setStyleSheet(pressed_style)
        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_stop)
        btn_layout.addWidget(self.btn_shuffle)
        btn_layout.addWidget(self.btn_repeat)
        main_layout.addLayout(btn_layout)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        main_layout.addWidget(line)
        self.status_label = QLabelWidget("Статус: готов")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("QLabel { background: #e0e0e0; border: 1px solid #b0b0b0; padding: 8px; font-weight: bold; }")
        main_layout.addWidget(self.status_label)
        self.setLayout(main_layout)
        font = QFont()
        font.setPointSize(11)
        self.setFont(font)
        self.load_settings()
        self.btn_group_manager.clicked.connect(self.open_group_manager)
        self.btn_start.clicked.connect(self.start_playlist)
        self.btn_stop.clicked.connect(self.stop_playlist)
        self.btn_shuffle.clicked.connect(self.shuffle_playlist)
        self.btn_repeat.clicked.connect(self.toggle_repeat)
        self.group_filter_list.itemChanged.connect(self.on_group_filter_changed)
        self.btn_resize.clicked.connect(self.change_player_size)
        self.btn_more.clicked.connect(self.show_logs)
        self.btn_more.clicked.connect(self.open_mini_controller)
        self.btn_more.clicked.connect(self.reset_settings)
        self.timer = QTimer()
        self.timer.timeout.connect(self.next_frame)
        self.timer.setInterval(30)
        self.timer2 = QTimer()
        self.timer2.timeout.connect(self.on_duration_timeout)
        self.timer2.setSingleShot(True)
        self.timer3 = QTimer()
        self.timer3.timeout.connect(self.next_file)
        self.timer3.setSingleShot(True)
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_status)
        self.status_timer.start(1000)
        self.setMinimumSize(self.win_width + 36, self.win_height + 200)
        self.video_win.resize_window(self.win_width, self.win_height)
        if self.main_window_geometry is not None and is_valid_geometry(self.main_window_geometry):
            try:
                geom = tuple(self.main_window_geometry)
                self.setGeometry(*geom)
            except Exception:
                pass
        if self._mini_ctrl_win and self.mini_ctrl_geometry is not None and is_valid_geometry(self.mini_ctrl_geometry):
            try:
                geom = tuple(self.mini_ctrl_geometry)
                self._mini_ctrl_win.setGeometry(*geom)
            except Exception:
                pass
        self.update_group_filter_list()
        self.update_playlist_view()
        self.save_settings()
        self.group_filter_list.itemClicked.connect(self.on_group_item_clicked)
        self.start_playlist()

    def open_group_manager(self):
        dlg = GroupManagerDialog(self, self.groups, self.group_schedules, self.update_group_filter_list)
        dlg.exec_()
        self.save_settings()
        self.update_group_filter_list()

    def update_group_filter_list(self):
        self.group_filter_list.clear()
        all_item = QListWidgetItem("Все")
        all_item.setFlags(all_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        all_item.setCheckState(Qt.CheckState.Checked)
        all_item.setData(Qt.ItemDataRole.UserRole, None)
        self.group_filter_list.addItem(all_item)
        for g in self.groups:
            schedule = self.group_schedules.get(g)
            is_interval = schedule and getattr(schedule, 'is_interval_group', False)
            display_name = f"⏱ {g}" if is_interval else g
            item = QListWidgetItem(display_name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, g)
            if is_interval:
                item.setForeground(QColor(30, 80, 200))
            self.group_filter_list.addItem(item)

    def on_group_filter_changed(self, item):
        if item.text() == "Все":
            block = self.group_filter_list.blockSignals
            block(True)
            state = item.checkState()
            for i in range(1, self.group_filter_list.count()):
                item = self.group_filter_list.item(i)
                if item is not None and hasattr(item, 'setCheckState'):
                    item.setCheckState(state)
            block(False)
        else:
            checked = any(
                (item is not None and hasattr(item, 'checkState') and item.checkState() == Qt.CheckState.Checked)
                for item in (self.group_filter_list.item(i) for i in range(1, self.group_filter_list.count()))
            )
            block = self.group_filter_list.blockSignals
            block(True)
            first_item = self.group_filter_list.item(0)
            if first_item is not None and hasattr(first_item, 'setCheckState') and callable(getattr(first_item, 'setCheckState', None)) and type(first_item).__name__ != 'Never':
                try:
                    first_item.setCheckState(Qt.CheckState.Checked if not checked else Qt.CheckState.Unchecked)
                except Exception:
                    pass
            block(False)
        self.filter_groups = set()
        for i in range(1, self.group_filter_list.count()):
            it = self.group_filter_list.item(i)
            if it is not None and hasattr(it, 'checkState') and it.checkState() == Qt.CheckState.Checked:
                group_name = it.data(Qt.ItemDataRole.UserRole) if hasattr(it, 'data') else None
                if group_name:
                    self.filter_groups.add(group_name)
        first_item = self.group_filter_list.item(0)
        try:
            if first_item is not None and hasattr(first_item, 'checkState') and callable(getattr(first_item, 'checkState', None)) and type(first_item).__name__ != 'Never' and first_item.checkState() == Qt.CheckState.Checked:
                self.filter_groups.clear()
        except Exception:
            pass
        self.update_playlist_view()

    def update_playlist_view(self):
        self.file_list.clear()
        if self._group_view_mode:
            for pi in self.all_items:
                if self._group_view_mode in pi.groups:
                    self.add_list_item(pi)
            return
        for pi in self.all_items:
            if not self.filter_groups:
                if any(
                    g in self.group_schedules and (
                        self.group_schedules[g] and not getattr(self.group_schedules[g], 'is_interval_group', False)
                    ) for g in pi.groups
                ):
                    self.add_list_item(pi)
            else:
                if any(
                    g in self.filter_groups and g in self.group_schedules and (
                        self.group_schedules[g] and not getattr(self.group_schedules[g], 'is_interval_group', False)
                    ) for g in pi.groups
                ):
                    self.add_list_item(pi)

    def add_file_to_group(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите файлы",
            "",
            "Видео и изображения (*.mp4 *.avi *.mov *.mkv *.jpg *.jpeg *.png *.bmp *.webp)"
        )
        if not file_paths:
            return
        if not self.groups:
            QMessageBox.warning(self, "Нет групп", "Сначала создайте хотя бы одну группу.")
            return
        group, ok = QInputDialog.getItem(self, "Выберите группу", "Группа:", self.groups, editable=False)
        if not (ok and group):
            return
        added = 0
        for file_path in file_paths:
            pi = next((item for item in self.all_items if item.path == file_path), None)
            if not pi:
                ext = os.path.splitext(file_path)[1].lower()
                pi = PlaylistItem(file_path, None if ext in SUPPORTED_VIDEO_EXTS else 3000, 1)
                self.all_items.append(pi)
            if group not in pi.groups:
                pi.groups.add(group)
                added += 1
            self.update_playlist_view()
            self.save_settings()
        self.status_label.setText(f'Добавлено файлов в группу "{group}": {added}')

    def start_playlist(self):
        # Разрешаем повторный старт, если нет активных групп или очередь пуста
        if not self.is_stopped and self.ord:
            print('[START_PLAYLIST] Уже запущено, повторный старт игнорируется')
            return
        print("[START_PLAYLIST] Запуск плейлиста")
        self._interval_playing_groups.clear()
        for timer in self.group_timers.values():
            timer.stop()
        self.group_timers = {}
        self.is_stopped = False
        self._group_view_mode = None
        self._interval_group_playing.clear()  # <--- очищаем активные интервальные группы при старте
        now = datetime.now()

        # Формируем очередь из файлов основных групп
        now_active_groups = set()
        for g, sch in self.group_schedules.items():
            if sch and sch.is_active_now() and not getattr(sch, 'is_interval_group', False):
                now_active_groups.add(g)

        self.ord = []
        if now_active_groups:
            if self.filter_groups:
                play_groups = now_active_groups.intersection(self.filter_groups)
            else:
                play_groups = now_active_groups
            self.ord = [i for i, pi in enumerate(self.all_items) if pi.groups.intersection(play_groups)]
            print(f"[START_PLAYLIST] Активные группы: {play_groups}, ord: {self.ord}")
            if not self.ord:
                self.status_label.setText("Нет элементов для выбранных групп.")
                print('Нет элементов для выбранных групп:', play_groups)
                return

        # Запускаем таймеры для интервальных групп
        for group_name, schedule in self.group_schedules.items():
            if schedule and schedule.is_interval_group and schedule.interval_minutes and schedule.is_active_now():
                # Всегда обновляем время последнего запуска и перезапускаем таймер
                self.last_interval_run[group_name] = now
                print(f"[START_PLAYLIST] Перезапуск таймера интервальной группы {group_name}")
                if group_name in self.group_timers:
                    self.group_timers[group_name].stop()
                    del self.group_timers[group_name]
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.setInterval(schedule.interval_minutes * 60 * 1000)
                timer.timeout.connect(partial(self.on_interval_group_trigger, group_name))
                timer.start()
                self.group_timers[group_name] = timer

        if self.ord:
            self.play_idx = -1
            self.next_file()
        else:
            self.status_label.setText("Нет активных основных групп, ожидание интервальных групп.")
            print("[START_PLAYLIST] Нет активных основных групп.")

    def on_interval_group_trigger(self, group_name):
        print(f"[INTERVAL_TRIGGER] Сработал таймер для группы {group_name}")
        if self.is_stopped:
            print("[INTERVAL_TRIGGER] Воспроизведение остановлено, игнорируем")
            return
        # Защита: если группа уже играет, не запускать повторно
        if group_name in self._interval_group_playing or group_name in self._interval_playing_groups:
            print(f"[INTERVAL_TRIGGER] Группа {group_name} уже проигрывается, повторный запуск запрещён")
            return
        # Остановить и удалить таймер
        if group_name in self.group_timers:
            self.group_timers[group_name].stop()
            del self.group_timers[group_name]
        group_indices = [i for i, pi in enumerate(self.all_items) if group_name in pi.groups]
        if not group_indices:
            print(f"[INTERVAL_TRIGGER] Нет файлов в группе {group_name}")
            return
        self._interval_playing_groups.add(group_name)
        self._interval_group_playing.add(group_name)
        # Вставляем файлы группы в очередь после текущего
        if self.play_idx == -1:
            self.ord = group_indices
            self.play_idx = -1
            self.next_file()
        else:
            current_idx = self.ord[self.play_idx]
            insert_pos = self.play_idx + 1
            self.ord[insert_pos:insert_pos] = group_indices
            print(f"[INTERVAL_TRIGGER] Вставлено {len(group_indices)} файлов группы {group_name} на позицию {insert_pos}")

    def next_file(self):
        if self._current_playing:
            self.log_end(self._current_playing)
        print(f"[NEXT_FILE] ord: {self.ord}, play_idx: {self.play_idx}, is_stopped: {self.is_stopped}")
        if self.is_stopped:
            return
        if not self.ord:
            self.status_label.setText("Нет файлов для воспроизведения.")
            print("Нет файлов для воспроизведения.")
            return
        self.play_idx += 1

        # Проверяем, завершились ли какие-либо интервальные группы
        finished_groups = set()
        for group_name in list(self._interval_playing_groups):
            # Группа считается завершённой, если следующий файл не из этой группы или очередь закончилась
            finished = False
            if self.play_idx >= len(self.ord):
                finished = True
            else:
                current_idx = self.ord[self.play_idx]
                pi = self.all_items[current_idx]
                if group_name not in pi.groups:
                    finished = True
            if finished:
                print(f"[NEXT_FILE] Интервальная группа {group_name} завершилась")
                schedule = self.group_schedules.get(group_name)
                if schedule and schedule.interval_minutes:
                    print(f"[NEXT_FILE] Перезапуск таймера для группы {group_name}")
                    if group_name in self.group_timers:
                        self.group_timers[group_name].stop()
                        del self.group_timers[group_name]
                    timer = QTimer(self)
                    timer.setSingleShot(True)
                    timer.setInterval(schedule.interval_minutes * 60 * 1000)
                    timer.timeout.connect(partial(self.on_interval_group_trigger, group_name))
                    timer.start()
                    self.group_timers[group_name] = timer
                    self.last_interval_run[group_name] = datetime.now()
                self._interval_group_playing.discard(group_name)
                finished_groups.add(group_name)
        self._interval_playing_groups -= finished_groups

        if self.play_idx >= len(self.ord):
            if self.btn_repeat.isChecked():
                now_active_groups = set()
                for g, sch in self.group_schedules.items():
                    if sch and sch.is_active_now() and not getattr(sch, 'is_interval_group', False):
                        now_active_groups.add(g)
                if now_active_groups:
                    if self.filter_groups:
                        play_groups = now_active_groups.intersection(self.filter_groups)
                    else:
                        play_groups = now_active_groups
                    self.ord = [i for i, pi in enumerate(self.all_items) if pi.groups.intersection(play_groups)]
                    if not self.ord:
                        self.status_label.setText("Нет файлов для активных групп.")
                        print("Нет файлов для активных групп.")
                        self.stop_playlist()
                        return
                    self.play_idx = 0
                else:
                    self.status_label.setText("Нет активных основных групп.")
                    print("Нет активных основных групп.")
                    self.stop_playlist()
                    return
            else:
                self.stop_playlist()
                return
        idx = self.ord[self.play_idx]
        pi = self.all_items[idx]
        self._current_playing = pi
        print(f"[NEXT_FILE] Воспроизводится файл: {pi.path}")
        ext = os.path.splitext(pi.path)[1].lower()
        self.log_start(pi)
        if ext in SUPPORTED_VIDEO_EXTS:
            self.start_video_thread(pi.path)
            self.timer2.stop()
            if pi.duration:
                self.timer2.start(pi.duration)
        else:
            self.timer.stop()
            self.timer2.stop()
            self.stop_video_thread()
            self.video_win.show_image(pi.path)
            dur = pi.duration if pi.duration is not None else 3000
            self.timer3.start(dur)

    def next_frame(self):
        if not self.cap:
            self.timer.stop()
            return
        result = self.cap.read() if self.cap is not None and hasattr(self.cap, 'read') else (None, None)
        ret, frame = result if isinstance(result, tuple) and len(result) == 2 else (None, None)
        if not ret:
            idx = self.ord[self.play_idx]
            pi = self.all_items[idx]
            pi._current_loop += 1
            if pi.loops == 0 or pi._current_loop < pi.loops:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                return
            else:
                if getattr(self, '_just_manual', False):
                    self._just_manual = False
                    return
                self.next_file()
                return
        self.video_win.show_frame(frame)

    def on_duration_timeout(self):
        if self.cap:
            self.cap.release()
            self.cap = None
            gc.collect()
        if getattr(self, '_just_manual', False):
            self._just_manual = False
            return
        self.next_file()

    def update_status(self):
        if self.is_stopped:
            return
        now = datetime.now()
        time_str = now.strftime("%H:%M:%S")
        day_str = DAYS_OF_WEEK[now.weekday()]
        active_groups = []
        next_times = []
        for group_name, schedule in self.group_schedules.items():
            if schedule and schedule.is_active_now():
                active_groups.append(group_name)
            if schedule and schedule.interval_minutes and getattr(schedule, 'is_interval_group', False):
                last_run = self.last_interval_run.get(group_name)
                if last_run:
                    ms_left = (last_run + timedelta(minutes=schedule.interval_minutes) - datetime.now()).total_seconds() * 1000
                    if ms_left < 0:
                        ms_left = 0
                    min_left = int(ms_left // 60000)
                    sec_left = int((ms_left // 1000) % 60)
                    next_times.append(f"{group_name}: через {min_left} мин {sec_left} сек")
                else:
                    next_times.append(f"{group_name}: ожидание")
        # --- Автоматическая перепроверка активных групп по времени ---
        if not hasattr(self, '_last_active_groups'):
            self._last_active_groups = set(active_groups)
        new_groups = set(active_groups) - self._last_active_groups
        if new_groups:
            print(f"[STATUS] Новые активные группы: {new_groups}")
            # Обычные группы (не интервальные)
            new_main_groups = {g for g in new_groups if not getattr(self.group_schedules.get(g), 'is_interval_group', False)}
            if new_main_groups:
                if not self.ord or self.play_idx == -1:
                    self.is_stopped = True
                    self.start_playlist()
                else:
                    for g in new_main_groups:
                        indices = [i for i, pi in enumerate(self.all_items) if g in pi.groups]
                        self.ord.extend(indices)
                    print(f"[STATUS] Добавлены файлы групп {new_main_groups} в конец очереди")
            # Интервальные группы: если активна и таймер не запущен — запустить таймер
            new_interval_groups = {g for g in new_groups if getattr(self.group_schedules.get(g), 'is_interval_group', False)}
            for g in new_interval_groups:
                if g not in self.group_timers:
                    schedule = self.group_schedules.get(g)
                    if schedule and schedule.interval_minutes:
                        print(f"[STATUS] Запускаю таймер интервальной группы {g}")
                        timer = QTimer(self)
                        timer.setSingleShot(True)
                        timer.setInterval(schedule.interval_minutes * 60 * 1000)
                        timer.timeout.connect(lambda g=g: self.on_interval_group_trigger(g))
                        timer.start()
                        self.group_timers[g] = timer
                        self.last_interval_run[g] = datetime.now()
        self._last_active_groups = set(active_groups)
        if self.filter_groups:
            shown_groups = [g for g in active_groups if g in self.filter_groups]
        else:
            shown_groups = active_groups
        status_lines = [f"Время: {time_str}, {day_str}"]
        if shown_groups:
            status_lines.append(f"Активные группы: {', '.join(shown_groups)}")
        else:
            status_lines.append("Нет активных групп по расписанию")
        if next_times:
            status_lines.append("След. запуск: " + "; ".join(next_times))
        status = "\n".join(status_lines)
        self.status_label.setText(status)
        self.status_label.setToolTip(status.replace("\n", "<br>"))

    def auto_select_groups(self, active_groups):
        self.sync_group_filter_checkboxes()

    def sync_group_filter_checkboxes(self):
        block = self.group_filter_list.blockSignals
        block(True)
        try:
            for i in range(self.group_filter_list.count()):
                item = self.group_filter_list.item(i)
                if item is None:
                    continue
                if i == 0:
                    if not self.filter_groups:
                        if hasattr(item, 'setCheckState'):
                            item.setCheckState(Qt.CheckState.Checked)
                    else:
                        if hasattr(item, 'setCheckState'):
                            item.setCheckState(Qt.CheckState.Unchecked)
                else:
                    group_name = item.data(Qt.ItemDataRole.UserRole) if hasattr(item, 'data') else None
                    if group_name in self.filter_groups:
                        if hasattr(item, 'setCheckState'):
                            item.setCheckState(Qt.CheckState.Checked)
                    else:
                        if hasattr(item, 'setCheckState'):
                            item.setCheckState(Qt.CheckState.Unchecked)
        finally:
            block(False)

    def file_context_menu(self, pos):
        li = self.file_list.itemAt(pos)
        if not li:
            return
        self.file_list.setCurrentItem(li)
        pi = li.data(Qt.ItemDataRole.UserRole)
        m = QMenu(self)
        m.addAction("Установить длительность и loops", lambda: self.edit_dur_loops(li))
        mg = m.addMenu("Добавить в группу")
        if mg is not None and hasattr(mg, 'addAction'):
            mg.addAction("Все", lambda: self.assign_all_groups(pi, li))
            for g in self.groups:
                mg.addAction(g, lambda gg=g: self.assign_group(pi, gg, li))
        mg_del = m.addMenu("Удалить из группы")
        if mg_del is not None and hasattr(mg_del, 'addAction'):
            for g in sorted(pi.groups):
                mg_del.addAction(g, lambda gg=g: self.remove_group_from_file(pi, gg, li))
        if self.file_list is not None and hasattr(self.file_list, 'viewport'):
            viewport = self.file_list.viewport()
            if viewport is not None and hasattr(viewport, 'mapToGlobal'):
                try:
                    m.exec_(viewport.mapToGlobal(pos))
                except Exception:
                    pass

    def assign_all_groups(self, pi, li=None):
        pi.groups.clear()
        self.status_label.setText('Файл удалён из всех групп.')
        self.update_playlist_view()

    def assign_group(self, pi, group, li=None):
        if group in pi.groups:
            pi.groups.remove(group)
            self.status_label.setText(f'Группа "{group}" удалена из файла.')
        else:
            pi.groups.add(group)
            self.status_label.setText(f'Группа "{group}" добавлена к файлу.')
        self.update_playlist_view()

    def remove_group_from_file(self, pi, group, li=None):
        if group in pi.groups:
            pi.groups.remove(group)
            self.status_label.setText(f'Файл удалён из группы "{group}".')
            self.update_playlist_view()
            self.save_settings()

    def change_player_size(self):
        w, ok1 = QInputDialog.getInt(self, "Ширина окна", "Введите ширину:", value=self.win_width, min=100, max=3840)
        if not ok1:
            return
        h, ok2 = QInputDialog.getInt(self, "Высота окна", "Введите высоту:", value=self.win_height, min=100, max=2160)
        if not ok2:
            return
        self.win_width = w
        self.win_height = h
        self.setMinimumSize(w + 36, h + 200)
        self.video_win.resize_window(w, h)
        self.status_label.setText(f"Размер плеера изменён: {w}x{h}")

    def log_start(self, pi):
        self._current_log = {
            'file': os.path.basename(pi.path),
            'groups': ','.join(sorted(pi.groups)) or '-',
            'start': datetime.now(),
        }

    def log_end(self, pi):
        if not self._current_log:
            return
        self._current_log['end'] = datetime.now()
        start = self._current_log['start']
        end = self._current_log['end']
        duration = (end - start).total_seconds()
        with open(self._log_file, 'a', encoding='utf-8') as f:
            f.write(f"{self._current_log['file']} | {self._current_log['groups']} | "
                    f"{start.strftime('%Y-%m-%d %H:%M:%S')} | {end.strftime('%Y-%m-%d %H:%M:%S')} | {duration:.2f} сек\n")
        self._current_log = None

    def show_logs(self):
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem, QDialogButtonBox, QHBoxLayout, QLineEdit, QPushButton, QLabel, QFileDialog, QDateEdit
        from PyQt5.QtCore import QDate
        import csv
        dlg = QDialog(self)
        dlg.setWindowTitle('Логи воспроизведения')
        layout = QVBoxLayout(dlg)
        filter_layout = QHBoxLayout()
        filter_file = QLineEdit()
        filter_file.setPlaceholderText('Фильтр по файлу')
        filter_group = QLineEdit()
        filter_group.setPlaceholderText('Фильтр по группе')
        today = QDate.currentDate()
        start_date = QDateEdit()
        start_date.setCalendarPopup(True)
        start_date.setDisplayFormat('dd/MM/yyyy')
        start_date.setSpecialValueText('')
        start_date.setMinimumDate(QDate(1970, 1, 1))
        start_date.setDate(today)
        end_date = QDateEdit()
        end_date.setCalendarPopup(True)
        end_date.setDisplayFormat('dd/MM/yyyy')
        end_date.setSpecialValueText('')
        end_date.setMinimumDate(QDate(1970, 1, 1))
        end_date.setDate(today)
        btn_clear_dates = QPushButton('Очистить даты')
        btn_export = QPushButton('Экспорт в CSV')
        filter_layout.addWidget(QLabel('Файл:'))
        filter_layout.addWidget(filter_file)
        filter_layout.addWidget(QLabel('Группа:'))
        filter_layout.addWidget(filter_group)
        filter_layout.addWidget(QLabel('С:'))
        filter_layout.addWidget(start_date)
        filter_layout.addWidget(QLabel('По:'))
        filter_layout.addWidget(end_date)
        filter_layout.addWidget(btn_clear_dates)
        filter_layout.addWidget(btn_export)
        layout.addLayout(filter_layout)
        table = QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(['Файл', 'Группы', 'Старт', 'Конец', 'Длительность (сек)'])
        table.setSortingEnabled(True)
        from PyQt5.QtWidgets import QHeaderView
        header = table.horizontalHeader()
        if header is not None and hasattr(header, 'setSectionResizeMode'):
            header.setSectionResizeMode(QHeaderView.Stretch)
        try:
            with open(self._log_file, 'r', encoding='utf-8') as f:
                lines = [line.strip() for line in f if line.strip()]
        except Exception:
            lines = []
        def parse_lines():
            data = []
            for line in lines:
                parts = [p.strip() for p in line.split('|')]
                if len(parts) < 5:
                    continue
                try:
                    dt = QDate.fromString(parts[2][:10], 'yyyy-MM-dd')
                    parts[2] = dt.toString('dd/MM/yyyy') + parts[2][10:]
                except Exception:
                    pass
                data.append(parts)
            return data
        all_data = parse_lines()
        def apply_filter():
            file_text = filter_file.text().lower()
            group_text = filter_group.text().lower()
            start_val = start_date.date()
            end_val = end_date.date()
            filtered = []
            for row in all_data:
                if file_text and file_text not in row[0].lower():
                    continue
                if group_text and group_text not in row[1].lower():
                    continue
                row_date = QDate.fromString(row[2][:10], 'dd/MM/yyyy')
                if start_val > QDate(1970, 1, 1) and row_date < start_val:
                    continue
                if end_val < QDate(2100, 12, 31) and row_date > end_val:
                    continue
                filtered.append(row)
            table.setRowCount(len(filtered))
            for r, row in enumerate(filtered):
                for c in range(5):
                    item = QTableWidgetItem(row[c] if c < len(row) else '')
                    item.setFlags(item.flags() ^ Qt.ItemFlag.ItemIsEditable)
                    table.setItem(r, c, item)
            table.resizeColumnsToContents()
        def on_filter_file_changed():
            self.filter_file_text = filter_file.text()
            apply_filter()
        def on_filter_group_changed():
            self.filter_group_text = filter_group.text()
            apply_filter()
        def on_start_date_changed():
            self.filter_start_date = start_date.date().toString('dd/MM/yyyy')
            apply_filter()
        def on_end_date_changed():
            self.filter_end_date = end_date.date().toString('dd/MM/yyyy')
            apply_filter()
        filter_file.textChanged.connect(on_filter_file_changed)
        filter_group.textChanged.connect(on_filter_group_changed)
        start_date.dateChanged.connect(on_start_date_changed)
        end_date.dateChanged.connect(on_end_date_changed)
        if self.filter_file_text:
            filter_file.setText(self.filter_file_text)
        if self.filter_group_text:
            filter_group.setText(self.filter_group_text)
        if self.filter_start_date:
            d = QDate.fromString(self.filter_start_date, 'dd/MM/yyyy')
            if d.isValid():
                start_date.setDate(d)
        if self.filter_end_date:
            d = QDate.fromString(self.filter_end_date, 'dd/MM/yyyy')
            if d.isValid():
                end_date.setDate(d)
        def clear_dates():
            start_date.setDate(QDate(1970, 1, 1))
            end_date.setDate(QDate(2100, 12, 31))
        btn_clear_dates.clicked.connect(clear_dates)
        def export_csv():
            path, _ = QFileDialog.getSaveFileName(dlg, 'Сохранить как CSV', 'logs.csv', 'CSV Files (*.csv)')
            if not path:
                return
            row_count = table.rowCount()
            col_count = table.columnCount()
            with open(path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f, delimiter=';')
                writer.writerow(['Файл', 'Группы', 'Старт', 'Конец', 'Длительность (сек)'])
                for r in range(row_count):
                    row = []
                    for c in range(col_count):
                        item = table.item(r, c)
                        row.append(item.text() if item is not None and hasattr(item, 'text') else '')
                    writer.writerow(row)
        btn_export.clicked.connect(export_csv)
        layout.addWidget(table)
        btns_layout = QHBoxLayout()
        btns_layout.addStretch()
        btn_clear_logs = QPushButton('Очистить логи')
        btns_layout.addWidget(btn_clear_logs)
        btns = QDialogButtonBox(QDialogButtonBox.Ok)
        btns.accepted.connect(dlg.accept)
        btns_layout.addWidget(btns)
        layout.addLayout(btns_layout)
        def clear_logs():
            import os
            try:
                with open(self._log_file, 'w', encoding='utf-8') as f:
                    pass
                nonlocal lines, all_data
                lines = []
                all_data = []
                apply_filter()
            except Exception as e:
                QMessageBox.warning(dlg, 'Ошибка', f'Не удалось очистить логи: {e}')
        btn_clear_logs.clicked.connect(clear_logs)
        dlg.resize(1200, 600)
        apply_filter()
        dlg.exec_()
        self.save_settings()

    def save_settings(self):
        data = {
            'win_width': self.win_width,
            'win_height': self.win_height,
            'groups': self.groups,
            'group_schedules': {g: self.serialize_schedule(s) for g, s in self.group_schedules.items()},
            'last_folder': self.last_folder,
            'filter_file_text': self.filter_file_text,
            'filter_group_text': self.filter_group_text,
            'filter_start_date': self.filter_start_date,
            'filter_end_date': self.filter_end_date,
            'main_window_geometry': list(self.geometry().getRect()),
            'files': [self.serialize_item(pi) for pi in self.all_items],
            'last_interval_run': {k: v.isoformat() for k, v in self.last_interval_run.items()},
            'mini_ctrl_geometry': list(self._mini_ctrl_win.geometry().getRect()) if self._mini_ctrl_win and self._mini_ctrl_win.isVisible() else None,
        }
        try:
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print('Ошибка сохранения настроек:', e)

    def load_settings(self):
        if not os.path.exists(SETTINGS_FILE):
            self.save_settings()
            return
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.win_width = data.get('win_width', self.win_width)
            self.win_height = data.get('win_height', self.win_height)
            self.groups = data.get('groups', [])
            self.group_schedules = {g: self.deserialize_schedule(s) for g, s in data.get('group_schedules', {}).items()}
            self.last_folder = data.get('last_folder', '')
            self.filter_file_text = data.get('filter_file_text', '')
            self.filter_group_text = data.get('filter_group_text', '')
            self.filter_start_date = data.get('filter_start_date')
            self.filter_end_date = data.get('filter_end_date')
            self.main_window_geometry = data.get('main_window_geometry')
            self.all_items = [self.deserialize_item(d) for d in data.get('files', [])]
            self.all_items = [pi for pi in self.all_items if os.path.exists(pi.path)]
            from datetime import datetime
            self.last_interval_run = {k: datetime.fromisoformat(v) for k, v in data.get('last_interval_run', {}).items()}
            today = date.today()
            self.last_interval_run = {k: v for k, v in self.last_interval_run.items() if v.date() == today}
            self.interval_check_timer.start(60_000)
            self.update_playlist_view()
            self.update_group_filter_list()
            for group_name, schedule in self.group_schedules.items():
                if schedule and schedule.is_interval_group and schedule.interval_minutes:
                    if schedule.is_active_now():
                        last_run = self.last_interval_run.get(group_name)
                        if not last_run:
                            self.last_interval_run[group_name] = datetime.now()
                    else:
                        if group_name in self.last_interval_run:
                            del self.last_interval_run[group_name]
            print("[LOAD_SETTINGS] group_schedules:", self.group_schedules)
            for g, s in self.group_schedules.items():
                if s and s.is_interval_group:
                    print(f"[LOAD_SETTINGS] Интервальная группа {g}: interval_minutes={s.interval_minutes}, days={s.days}")
            self.mini_ctrl_geometry = data.get('mini_ctrl_geometry')
        except Exception as e:
            print('Ошибка загрузки настроек:', e)

    def reset_settings(self):
        import sys, os, subprocess
        if os.path.exists(SETTINGS_FILE):
            try:
                os.remove(SETTINGS_FILE)
            except Exception as e:
                print('Ошибка удаления настроек:', e)
        python = sys.executable
        subprocess.Popen([python] + sys.argv)
        sys.exit()

    def moveEvent(self, event):
        super().moveEvent(event)
        self.main_window_geometry = list(self.geometry().getRect())
        self.save_settings()
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.main_window_geometry = list(self.geometry().getRect())
        self.save_settings()
    def closeEvent(self, event):
        from PyQt5.QtWidgets import QApplication
        QApplication.quit()
        event.accept()

    def serialize_schedule(self, s):
        if s is None:
            return None
        return {
            'start_time': s.start_time.toString('HH:mm') if s.start_time else None,
            'end_time': s.end_time.toString('HH:mm') if s.end_time else None,
            'days': s.days,
            'interval_minutes': s.interval_minutes,
            'is_interval_group': s.is_interval_group
        }

    def deserialize_schedule(self, d):
        if d is None:
            return None
        from PyQt5.QtCore import QTime
        return GroupSchedule(
            start_time=QTime.fromString(d.get('start_time'), 'HH:mm') if d.get('start_time') else None,
            end_time=QTime.fromString(d.get('end_time'), 'HH:mm') if d.get('end_time') else None,
            days=d.get('days', []),
            interval_minutes=d.get('interval_minutes'),
            is_interval_group=d.get('is_interval_group', False)
        )

    def serialize_item(self, pi):
        return {
            'path': pi.path,
            'duration': pi.duration,
            'loops': pi.loops,
            'groups': list(pi.groups)
        }

    def deserialize_item(self, d):
        pi = PlaylistItem(d['path'], d.get('duration'), d.get('loops', 1))
        pi.groups = set(d.get('groups', []))
        return pi

    def open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку с медиафайлами", self.last_folder or "")
        if not folder:
            self.status_label.setText("Выбор папки отменён.")
            return
        self.last_folder = folder
        added = 0
        for f in sorted(os.listdir(folder)):
            ext = os.path.splitext(f)[1].lower()
            if ext in SUPPORTED_EXTS:
                path = os.path.join(folder, f)
                if not any(pi.path == path for pi in self.all_items):
                    pi = PlaylistItem(path, None if ext in SUPPORTED_VIDEO_EXTS else 3000, 1)
                    self.all_items.append(pi)
                    added += 1
        self.update_playlist_view()
        self.save_settings()
        if added:
            self.status_label.setText(f"Добавлено файлов: {added}")
        else:
            self.status_label.setText("Все файлы из папки уже добавлены.")

    def edit_dur_loops(self, li):
        pi = li.data(Qt.ItemDataRole.UserRole)
        dur_sec, ok1 = QInputDialog.getInt(
            self, "Длительность (сек)", "Введите длительность в секундах (0 = авто):",
            value=int(pi.duration / 1000) if pi.duration is not None else 0, min=0, max=3600*10
        )
        if not ok1:
            return
        loops, ok2 = QInputDialog.getInt(
            self, "Повторы", "Введите количество повторов (0 = бесконечно):",
            value=pi.loops, min=0, max=1000
        )
        if not ok2:
            return
        pi.duration = dur_sec * 1000 if dur_sec > 0 else None
        pi.loops = loops
        self.update_playlist_view()
        self.save_settings()
        self.status_label.setText(f'Длительность и повторы обновлены: {dur_sec} сек, loops={loops}')

    def on_group_item_clicked(self, item):
        group_name = item.data(Qt.ItemDataRole.UserRole)
        if group_name is None:
            self._group_view_mode = None
        else:
            self._group_view_mode = group_name
        self.update_playlist_view()

    def check_interval_groups(self):
        if self.is_stopped:
            print("[CHECK_INTERVAL] Воспроизведение остановлено")
            return
        now = datetime.now()
        for group_name, schedule in self.group_schedules.items():
            if not schedule or not schedule.is_interval_group or not schedule.interval_minutes:
                continue
            if not schedule.is_active_now():
                if group_name in self.last_interval_run:
                    del self.last_interval_run[group_name]
                print(f"[CHECK_INTERVAL] Группа {group_name} не активна по расписанию")
                continue
            last_run = self.last_interval_run.get(group_name)
            time_elapsed = (now - last_run).total_seconds() if last_run else float('inf')
            print(f"[CHECK_INTERVAL] Группа {group_name}, last_run: {last_run}, time_elapsed: {time_elapsed}, interval: {schedule.interval_minutes * 60}")
            if not last_run or time_elapsed >= schedule.interval_minutes * 60:
                self.last_interval_run[group_name] = now
                self.on_interval_group_trigger(group_name)

    def start_video_thread(self, video_path):
        print(f'[Controller] start_video_thread: {video_path}')
        if self.video_thread:
            self.video_thread.stop()
        self.video_thread = VideoThread(video_path, self.win_width, self.win_height)
        self.video_thread.frame_ready.connect(self.video_win.show_frame)
        self.video_thread.video_finished.connect(self.on_video_finished)
        self.video_thread.start()

    def stop_video_thread(self):
        print(f'[Controller] stop_video_thread')
        if self.video_thread:
            self.video_thread.stop()
            self.video_thread = None

    def on_video_finished(self):
        print(f'[Controller] on_video_finished')
        if self.video_thread and getattr(self.video_thread, 'frames_shown', 0) == 0:
            print('[Controller] on_video_finished: no frames shown, skipping repeat')
            if self._current_playing:
                self._current_playing._current_loop = 0
            # Если это был ручной переход, не вызываем next_file повторно
            if getattr(self, 'manual_skip', False):
                self.manual_skip = False
                return
            self.next_file()
            return
        if getattr(self, '_just_manual', False):
            self._just_manual = False
            print(f'[Controller] on_video_finished: manual skip, do nothing')
            return
        if self._current_playing:
            self._current_playing._current_loop += 1
            if self._current_playing.loops == 0 or self._current_playing._current_loop < self._current_playing.loops:
                print(f'[Controller] on_video_finished: repeating video, loop {self._current_playing._current_loop}')
                self.start_video_thread(self._current_playing.path)
                return
            else:
                self._current_playing._current_loop = 0
        self.next_file()

    def play_group_playlist(self, group_name):
        print(f"[PLAY_GROUP_PLAYLIST] Запуск группы {group_name}")
        group_indices = [i for i, pi in enumerate(self.all_items) if group_name in pi.groups]
        if not group_indices:
            print(f"[PLAY_GROUP_PLAYLIST] Нет файлов для группы {group_name}")
            self.status_label.setText(f"Нет файлов для интервальной группы {group_name}.")
            return
        if self.play_idx == -1:
            self.ord = group_indices
            self.play_idx = -1
        else:
            insert_pos = self.play_idx + 1
            self.ord[insert_pos:insert_pos] = group_indices
            print(f"[PLAY_GROUP_PLAYLIST] Вставлено {len(group_indices)} файлов группы {group_name} на позицию {insert_pos}")
        self.next_file()

    def open_mini_controller(self):
        if self._mini_ctrl_win is None or not self._mini_ctrl_win.isVisible():
            self._mini_ctrl_win = MiniControllerWindow(self)
            if self.mini_ctrl_geometry is not None and is_valid_geometry(self.mini_ctrl_geometry):
                try:
                    geom = tuple(self.mini_ctrl_geometry)
                    self._mini_ctrl_win.setGeometry(*geom)
                except Exception:
                    pass
            self._mini_ctrl_win.show()
        else:
            self._mini_ctrl_win.raise_()
            self._mini_ctrl_win.activateWindow()

    def stop_playlist(self):
        if self._current_playing:
            self.log_end(self._current_playing)
        print("[STOP_PLAYLIST] Остановка плейлиста")
        self.timer.stop()
        self.timer2.stop()
        self.timer3.stop()
        self.stop_video_thread()
        self.status_label.setText("Воспроизведение остановлено.")
        self.play_idx = -1
        self._current_playing = None
        self._interval_playing_groups.clear()
        self.pending_interval_groups.clear()
        self.last_interval_run.clear()
        self._interval_group_playing.clear()  # <--- очищаем активные интервальные группы при остановке
        for timer in self.group_timers.values():
            timer.stop()
        self.group_timers.clear()
        self.interval_check_timer.stop()
        self.is_stopped = True
        self.ord = []

    def add_list_item(self, pi):
        txt = self.build_label(pi)
        li = QListWidgetItem(txt)
        li.setData(Qt.ItemDataRole.UserRole, pi)
        li.setToolTip('ПКМ — назначить группу, изменить длительность и повторы')
        self.file_list.addItem(li)

    def build_label(self, pi):
        fn = os.path.basename(pi.path)
        dur = f"{pi.duration / 1000:.1f}с" if pi.duration else "видео"
        loops = "∞" if pi.loops == 0 else str(pi.loops)
        gs = ", ".join(pi.groups) or "-"
        return f"{fn} [{dur}, loops={loops}, groups={gs}]"

    def shuffle_playlist(self):
        import random
        if self.ord:
            # Сохраняем текущий файл, чтобы не перемешивать его
            if self.play_idx >= 0 and self.play_idx < len(self.ord):
                current = self.ord[self.play_idx]
                rest = self.ord[:self.play_idx] + self.ord[self.play_idx + 1:]
                random.shuffle(rest)
                self.ord = rest[:self.play_idx] + [current] + rest[self.play_idx:]
            else:
                random.shuffle(self.ord)
            self.status_label.setText("Плейлист перемешан.")
        else:
            self.status_label.setText("Плейлист пуст, нечего перемешивать.")

    def toggle_repeat(self):
        self.status_label.setText("Режим повтора переключён.")

class PlaylistListWidget(QListWidget):
    def __init__(self, parent_ctrl, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.parent_ctrl = parent_ctrl

    def dropEvent(self, event):
        super().dropEvent(event)
        # После перемещения — синхронизировать self.all_items с новым порядком
        new_order = []
        for i in range(self.count()):
            li = self.item(i)
            if li is not None:
                pi = li.data(Qt.ItemDataRole.UserRole)
                if pi:
                    new_order.append(pi)
        # Сохраняем только те элементы, которые отображаются (фильтрованные)
        rest = [pi for pi in self.parent_ctrl.all_items if pi not in new_order]
        self.parent_ctrl.all_items = new_order + rest
        self.parent_ctrl.save_settings()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    ctrl = Controller()
    ctrl.show()
    sys.exit(app.exec_())