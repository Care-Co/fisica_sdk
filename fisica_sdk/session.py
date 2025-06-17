from .analyzer import analyze

class SessionReport:
    def __init__(self, metadata=None):
        self.metadata = metadata or {}
        self.frames = []
        self._frozen = False

    def set_metadata(self, metadata: dict):
        self.metadata = metadata

    def add_frame(self, frame):
        if not self._frozen:
            self.frames.append(frame)

    def add_frames(self, frame_list):
        if not self._frozen:
            self.frames.extend(frame_list)

    def freeze(self):
        self._frozen = True

    def is_frozen(self):
        return self._frozen