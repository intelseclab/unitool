import os
from PyQt6.QtCore import QThread, pyqtSignal
from .translations import tr


class Deleter(QThread):
    progress = pyqtSignal(int, str)   # pct, status message
    finished = pyqtSignal(list, list) # deleted_paths, error_strings

    def __init__(self, paths: list[str], parent=None):
        super().__init__(parent)
        self._paths = paths

    def run(self):
        import send2trash
        deleted, errors = [], []
        total = len(self._paths)

        for i, path in enumerate(self._paths):
            self.progress.emit(
                int(i / total * 100),
                tr('del_progress', done=i + 1, total=total,
                   name=os.path.basename(path)),
            )
            try:
                send2trash.send2trash(os.path.normpath(path))
                deleted.append(path)
            except Exception as exc:
                errors.append(f'{path}\n  → {exc}')

        self.progress.emit(100, tr('status_done', n=len(deleted)))
        self.finished.emit(deleted, errors)
