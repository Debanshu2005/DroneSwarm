from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, 
    QLabel, QLineEdit, QPushButton, QFormLayout, QComboBox, QTextEdit, QMessageBox
)
import os

class SettingsPanel(QWidget):
    """
    Configuration panel for GroundStation global settings,
    network parameters, and log verbosity. Reads/Writes YAML configs directly.
    """
    def __init__(self, network_manager):
        super().__init__()
        self.network_manager = network_manager
        self.config_dir = os.path.join(os.path.dirname(__file__), "..", "configs")
        self.config_dir = os.path.abspath(self.config_dir)
        self.init_ui()
        self.load_files_list()

    def init_ui(self):
        layout = QVBoxLayout(self)
        
        # 1. Config Editor
        net_group = QGroupBox("Configuration Editor")
        form = QVBoxLayout(net_group)
        
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("Select Config File:"))
        self.file_combo = QComboBox()
        self.file_combo.currentTextChanged.connect(self.load_selected_file)
        top_bar.addWidget(self.file_combo)
        
        btn_reload = QPushButton("Reload")
        btn_reload.clicked.connect(self.load_selected_file)
        top_bar.addWidget(btn_reload)
        
        form.addLayout(top_bar)
        
        self.editor = QTextEdit()
        self.editor.setStyleSheet("background-color: #1a1a1a; color: #d4d4d4; font-family: monospace; font-size: 14px;")
        form.addWidget(self.editor)
        
        layout.addWidget(net_group)
        
        # Buttons
        btn_layout = QHBoxLayout()
        save_btn = QPushButton("Save YAML")
        save_btn.setStyleSheet("background-color: #007bff; color: white; font-weight: bold;")
        save_btn.clicked.connect(self.save_file)
        
        btn_layout.addStretch()
        btn_layout.addWidget(save_btn)
        
        layout.addLayout(btn_layout)

    def load_files_list(self):
        if not os.path.exists(self.config_dir):
            return
        
        self.file_combo.clear()
        for f in sorted(os.listdir(self.config_dir)):
            if f.endswith(".yaml"):
                self.file_combo.addItem(f)

    def load_selected_file(self):
        filename = self.file_combo.currentText()
        if not filename:
            return
            
        path = os.path.join(self.config_dir, filename)
        try:
            with open(path, "r") as f:
                self.editor.setPlainText(f.read())
        except Exception as e:
            self.editor.setPlainText(f"Error loading {filename}: {e}")

    def save_file(self):
        filename = self.file_combo.currentText()
        if not filename:
            return
            
        path = os.path.join(self.config_dir, filename)
        try:
            with open(path, "w") as f:
                f.write(self.editor.toPlainText())
            QMessageBox.information(self, "Success", f"Saved {filename} successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save {filename}:\n{e}")
