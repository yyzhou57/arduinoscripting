import json
import os
import threading
import time
import queue
import tkinter as tk
from tkinter import ttk

import serial
import serial.tools.list_ports
from pynput import mouse, keyboard

PRESETS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets.json")
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")


class LineApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Leonardo Line Control")

        self.ser = None
        self.mouse_listener = None
        self.keyboard_listener = None
        self.reader_thread = None
        self.stop_reader = threading.Event()
        self.status_queue = queue.Queue()

        self.left_down = False
        self.right_down = False
        self.combo_active = False
        self.macro_enabled = False  # gate on the left+right click combo, toggled by double-tap 1/2

        self.presets = {"A": [], "D": []}  # list of {"name", "x", "y"} per group

        self.double_press_interval_ms = 400  # max gap between taps to count as a double-press
        self.last_key_time = {"1": 0.0, "2": 0.0}

        self.build_ui()
        self.load_presets()
        self.load_last_values()
        self.poll_queue()

    def build_ui(self):
        frame = ttk.Frame(self.root, padding=12)
        frame.grid()

        ttk.Label(frame, text="Port:").grid(column=0, row=0, sticky="w")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(frame, textvariable=self.port_var, width=15)
        self.port_combo.grid(column=1, row=0, sticky="w")
        self.refresh_ports()

        ttk.Button(frame, text="Refresh", command=self.refresh_ports).grid(column=2, row=0, padx=4)
        self.connect_btn = ttk.Button(frame, text="Connect", command=self.toggle_connect)
        self.connect_btn.grid(column=3, row=0, padx=4)

        ttk.Label(frame, text="Y value (+down / -up):").grid(column=0, row=1, sticky="w", pady=(10, 0))
        self.y_var = tk.StringVar(value="-5")
        self.y_entry = ttk.Entry(frame, textvariable=self.y_var, width=10)
        self.y_entry.grid(column=1, row=1, sticky="w", pady=(10, 0))
        self.y_var.trace_add("write", self.apply_values)

        ttk.Label(frame, text="X value (+right / -left):").grid(column=0, row=2, sticky="w")
        self.x_var = tk.StringVar(value="0")
        self.x_entry = ttk.Entry(frame, textvariable=self.x_var, width=10)
        self.x_entry.grid(column=1, row=2, sticky="w")
        self.x_var.trace_add("write", self.apply_values)

        ttk.Label(frame, text="Smoothness (0 = straight, 10 = very jittery):").grid(
            column=0, row=3, sticky="w", pady=(10, 0)
        )
        self.smoothness_var = tk.StringVar(value="0")
        self.smoothness_entry = ttk.Entry(frame, textvariable=self.smoothness_var, width=10)
        self.smoothness_entry.grid(column=1, row=3, sticky="w", pady=(10, 0))
        self.smoothness_var.trace_add("write", self.apply_values)

        ttk.Label(frame, text="Decimals allowed (e.g. 2.5). X and Y combine for diagonals.", foreground="gray").grid(
            column=0, row=4, columnspan=4, sticky="w", pady=(2, 0)
        )

        self.status_var = tk.StringVar(value="Disconnected")
        ttk.Label(frame, text="Status:").grid(column=0, row=5, sticky="w", pady=(10, 0))
        ttk.Label(frame, textvariable=self.status_var, foreground="blue").grid(
            column=1, row=5, columnspan=3, sticky="w", pady=(10, 0)
        )

        ttk.Label(frame, text="Hold left+right click together to draw, release either to stop.", foreground="gray").grid(
            column=0, row=6, columnspan=4, pady=(10, 0)
        )

        ttk.Label(
            frame,
            text="Double-tap 1 to enable the left+right click macro, double-tap 2 to disable it.",
            foreground="gray",
        ).grid(column=0, row=7, columnspan=4, pady=(2, 0))

        self.configure_btn = ttk.Button(frame, text="Configure", command=self.open_add_popup)
        self.configure_btn.grid(column=0, row=8, sticky="w", pady=(10, 0))

        ttk.Button(frame, text="Settings", command=self.open_settings_popup).grid(
            column=1, row=8, sticky="w", pady=(10, 0)
        )

        self.notes_var = tk.StringVar(value="")
        notes_frame = ttk.LabelFrame(frame, text="Notes", padding=6)
        notes_frame.grid(column=0, row=9, columnspan=4, sticky="we", pady=(8, 0))
        ttk.Label(notes_frame, textvariable=self.notes_var, foreground="blue", wraplength=380, justify="left").grid(
            column=0, row=0, sticky="w"
        )

        ttk.Label(frame, text="Click a preset to apply it, double-click to edit.", foreground="gray").grid(
            column=0, row=10, columnspan=4, sticky="w", pady=(6, 0)
        )

        hud_frame = ttk.Frame(frame)
        hud_frame.grid(column=0, row=11, columnspan=4, sticky="we", pady=(4, 0))

        a_frame = ttk.LabelFrame(hud_frame, text="Group A", padding=6)
        a_frame.grid(column=0, row=0, sticky="nw", padx=(0, 6))
        self.group_a_box_frame = ttk.Frame(a_frame)
        self.group_a_box_frame.grid(column=0, row=0)

        d_frame = ttk.LabelFrame(hud_frame, text="Group D", padding=6)
        d_frame.grid(column=1, row=0, sticky="nw")
        self.group_d_box_frame = ttk.Frame(d_frame)
        self.group_d_box_frame.grid(column=0, row=0)

    def refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def toggle_connect(self):
        if self.ser is None:
            self.connect()
        else:
            self.disconnect()

    def connect(self):
        port = self.port_var.get()
        if not port:
            self.status_var.set("No port selected")
            return
        try:
            self.ser = serial.Serial(port, 9600, timeout=1)
        except Exception as e:
            self.status_var.set(f"Error: {e}")
            self.ser = None
            return

        self.status_var.set("Connected - Listening")
        self.connect_btn.config(text="Disconnect")

        self.apply_values()

        self.stop_reader.clear()
        self.reader_thread = threading.Thread(target=self.read_serial, daemon=True)
        self.reader_thread.start()

        self.mouse_listener = mouse.Listener(on_click=self.on_click)
        self.mouse_listener.start()

        self.keyboard_listener = keyboard.Listener(on_press=self.on_key_press)
        self.keyboard_listener.start()

    def disconnect(self):
        if self.mouse_listener:
            self.mouse_listener.stop()
            self.mouse_listener = None
        if self.keyboard_listener:
            self.keyboard_listener.stop()
            self.keyboard_listener = None
        self.stop_reader.set()
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        self.left_down = False
        self.right_down = False
        self.combo_active = False
        self.macro_enabled = False
        self.connect_btn.config(text="Connect")
        self.status_var.set("Disconnected")

    def on_click(self, x, y, button, pressed):
        if not self.ser:
            return
        if button == mouse.Button.left:
            self.left_down = pressed
        elif button == mouse.Button.right:
            self.right_down = pressed
        else:
            return

        if not self.macro_enabled:
            return

        both_down = self.left_down and self.right_down
        if both_down and not self.combo_active:
            self.combo_active = True
            self.send_command("S")
        elif not both_down and self.combo_active:
            self.combo_active = False
            self.send_command("E")

    def on_key_press(self, key):
        if not self.ser:
            return
        try:
            char = key.char
        except AttributeError:
            return
        if char not in ("1", "2"):
            return

        now = time.time()
        last = self.last_key_time.get(char, 0.0)
        self.last_key_time[char] = now
        if (now - last) * 1000 > self.double_press_interval_ms:
            return

        self.last_key_time[char] = 0.0  # consume the pair so a 3rd tap needs a fresh double
        if char == "1":
            self.macro_enabled = True
        else:
            self.macro_enabled = False
            if self.combo_active:
                self.combo_active = False
                self.send_command("E")

    def open_settings_popup(self):
        popup = tk.Toplevel(self.root)
        popup.title("Settings")
        popup.grab_set()

        pframe = ttk.Frame(popup, padding=12)
        pframe.grid()

        ttk.Label(pframe, text="Double-press interval (ms):").grid(column=0, row=0, sticky="w")
        interval_var = tk.StringVar(value=str(self.double_press_interval_ms))
        ttk.Entry(pframe, textvariable=interval_var, width=10).grid(column=1, row=0, sticky="w")

        error_var = tk.StringVar()
        ttk.Label(pframe, textvariable=error_var, foreground="red").grid(
            column=0, row=1, columnspan=2, sticky="w", pady=(4, 0)
        )

        def do_save():
            try:
                interval = int(float(interval_var.get()))
            except ValueError:
                error_var.set("Invalid number")
                return
            if interval <= 0:
                error_var.set("Interval must be positive")
                return
            self.double_press_interval_ms = interval
            popup.destroy()

        btn_frame = ttk.Frame(pframe)
        btn_frame.grid(column=0, row=2, columnspan=2, pady=(10, 0))
        ttk.Button(btn_frame, text="Save", command=do_save).grid(column=0, row=0, padx=4)
        ttk.Button(btn_frame, text="Cancel", command=popup.destroy).grid(column=1, row=0, padx=4)

        self.center_popup(popup)

    def send_command(self, text):
        if self.ser:
            try:
                self.ser.write((text + "\n").encode())
            except Exception:
                pass

    def read_serial(self):
        while not self.stop_reader.is_set() and self.ser:
            try:
                line = self.ser.readline().decode(errors="ignore").strip()
            except Exception:
                break
            if line:
                self.status_queue.put(line)

    def poll_queue(self):
        try:
            while True:
                line = self.status_queue.get_nowait()
                if line == "DRAWING":
                    self.status_var.set("Drawing...")
                elif line in ("STOPPED", "DONE"):
                    self.status_var.set("Connected - Listening")
        except queue.Empty:
            pass
        self.root.after(100, self.poll_queue)

    def apply_values(self, *args):
        try:
            x = float(self.x_var.get())
            y = float(self.y_var.get())
            smoothness = float(self.smoothness_var.get())
        except ValueError:
            return  # ignore incomplete input while the user is still typing
        self.send_command(f"X{x}")
        self.send_command(f"Y{y}")
        self.send_command(f"M{smoothness}")
        self.save_last_values(x, y)

    def load_last_values(self):
        try:
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        if "x" in data:
            self.x_var.set(str(data["x"]))
        if "y" in data:
            self.y_var.set(str(data["y"]))

    def save_last_values(self, x, y):
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump({"x": x, "y": y}, f)
        except OSError:
            pass

    def load_presets(self):
        try:
            with open(PRESETS_FILE, "r") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return

        for group in ("A", "D"):
            for preset in data.get(group, []):
                self.presets[group].append(
                    {
                        "name": preset["name"],
                        "x": preset["x"],
                        "y": preset["y"],
                        "notes": preset.get("notes", ""),
                    }
                )
            self.refresh_group_display(group)

    def refresh_group_display(self, group):
        container = self.group_a_box_frame if group == "A" else self.group_d_box_frame
        for widget in container.winfo_children():
            widget.destroy()

        sorted_items = sorted(enumerate(self.presets[group]), key=lambda item: item[1]["name"].lower())
        for pos, (index, preset) in enumerate(sorted_items):
            btn = ttk.Button(container, text=preset["name"], width=14, command=lambda i=index: self.apply_preset(group, i))
            btn.grid(row=pos // 7, column=pos % 7, padx=2, pady=2)
            btn.bind("<Double-Button-1>", lambda e, i=index: self.open_edit_popup(group, i))

    def apply_preset(self, group, index):
        preset = self.presets[group][index]
        self.x_var.set(str(preset["x"]))
        self.y_var.set(str(preset["y"]))
        self.notes_var.set(preset.get("notes", ""))
        self.apply_values()

    def save_presets(self):
        try:
            with open(PRESETS_FILE, "w") as f:
                json.dump(self.presets, f, indent=2)
        except OSError:
            pass

    def center_popup(self, popup):
        popup.update_idletasks()
        width = popup.winfo_width()
        height = popup.winfo_height()
        x = (popup.winfo_screenwidth() - width) // 2
        y = (popup.winfo_screenheight() - height) // 2
        popup.geometry(f"+{x}+{y}")

    def create_preset(self, group, name, x, y, notes=""):
        self.presets[group].append({"name": name, "x": x, "y": y, "notes": notes})
        self.refresh_group_display(group)
        self.save_presets()

    def open_add_popup(self):
        popup = tk.Toplevel(self.root)
        popup.title("Add Preset")
        popup.grab_set()

        pframe = ttk.Frame(popup, padding=12)
        pframe.grid()

        ttk.Label(pframe, text="Group:").grid(column=0, row=0, sticky="w")
        group_var = tk.StringVar(value="A")
        ttk.Radiobutton(pframe, text="A", variable=group_var, value="A").grid(column=1, row=0, sticky="w")
        ttk.Radiobutton(pframe, text="D", variable=group_var, value="D").grid(column=2, row=0, sticky="w")

        ttk.Label(pframe, text="Name:").grid(column=0, row=1, sticky="w", pady=(6, 0))
        name_var = tk.StringVar()
        ttk.Entry(pframe, textvariable=name_var, width=18).grid(
            column=1, row=1, columnspan=2, sticky="w", pady=(6, 0)
        )

        ttk.Label(pframe, text="Vertical (Y):").grid(column=0, row=2, sticky="w", pady=(6, 0))
        y_var = tk.StringVar(value="-5")
        ttk.Entry(pframe, textvariable=y_var, width=10).grid(column=1, row=2, sticky="w", pady=(6, 0))

        ttk.Label(pframe, text="Horizontal (X):").grid(column=0, row=3, sticky="w", pady=(6, 0))
        x_var = tk.StringVar(value="0")
        ttk.Entry(pframe, textvariable=x_var, width=10).grid(column=1, row=3, sticky="w", pady=(6, 0))

        ttk.Label(pframe, text="Notes:").grid(column=0, row=4, sticky="nw", pady=(6, 0))
        notes_text = tk.Text(pframe, width=24, height=3)
        notes_text.grid(column=1, row=4, columnspan=2, sticky="w", pady=(6, 0))

        error_var = tk.StringVar()
        ttk.Label(pframe, textvariable=error_var, foreground="red").grid(
            column=0, row=5, columnspan=3, sticky="w", pady=(4, 0)
        )

        def do_add():
            name = name_var.get().strip()
            if not name:
                error_var.set("Name required")
                return
            try:
                x = float(x_var.get())
                y = float(y_var.get())
            except ValueError:
                error_var.set("Invalid number")
                return
            notes = notes_text.get("1.0", "end-1c").strip()
            self.create_preset(group_var.get(), name, x, y, notes)
            popup.destroy()

        btn_frame = ttk.Frame(pframe)
        btn_frame.grid(column=0, row=6, columnspan=3, pady=(10, 0))
        ttk.Button(btn_frame, text="Add", command=do_add).grid(column=0, row=0, padx=4)
        ttk.Button(btn_frame, text="Cancel", command=popup.destroy).grid(column=1, row=0, padx=4)

        self.center_popup(popup)

    def open_edit_popup(self, group, index):
        preset = self.presets[group][index]

        popup = tk.Toplevel(self.root)
        popup.title(f"Edit Preset - {preset['name']}")
        popup.grab_set()

        pframe = ttk.Frame(popup, padding=12)
        pframe.grid()

        ttk.Label(pframe, text="Group:").grid(column=0, row=0, sticky="w")
        group_var = tk.StringVar(value=group)
        ttk.Radiobutton(pframe, text="A", variable=group_var, value="A").grid(column=1, row=0, sticky="w")
        ttk.Radiobutton(pframe, text="D", variable=group_var, value="D").grid(column=2, row=0, sticky="w")

        ttk.Label(pframe, text="Name:").grid(column=0, row=1, sticky="w", pady=(6, 0))
        name_var = tk.StringVar(value=preset["name"])
        ttk.Entry(pframe, textvariable=name_var, width=18).grid(
            column=1, row=1, columnspan=2, sticky="w", pady=(6, 0)
        )

        ttk.Label(pframe, text="Vertical (Y):").grid(column=0, row=2, sticky="w", pady=(6, 0))
        y_var = tk.StringVar(value=str(preset["y"]))
        ttk.Entry(pframe, textvariable=y_var, width=10).grid(column=1, row=2, sticky="w", pady=(6, 0))

        ttk.Label(pframe, text="Horizontal (X):").grid(column=0, row=3, sticky="w", pady=(6, 0))
        x_var = tk.StringVar(value=str(preset["x"]))
        ttk.Entry(pframe, textvariable=x_var, width=10).grid(column=1, row=3, sticky="w", pady=(6, 0))

        ttk.Label(pframe, text="Notes:").grid(column=0, row=4, sticky="nw", pady=(6, 0))
        notes_text = tk.Text(pframe, width=24, height=3)
        notes_text.insert("1.0", preset.get("notes", ""))
        notes_text.grid(column=1, row=4, columnspan=2, sticky="w", pady=(6, 0))

        error_var = tk.StringVar()
        ttk.Label(pframe, textvariable=error_var, foreground="red").grid(
            column=0, row=5, columnspan=3, sticky="w", pady=(4, 0)
        )

        def do_save():
            new_name = name_var.get().strip()
            if not new_name:
                error_var.set("Name required")
                return
            try:
                new_x = float(x_var.get())
                new_y = float(y_var.get())
            except ValueError:
                error_var.set("Invalid number")
                return
            new_notes = notes_text.get("1.0", "end-1c").strip()

            new_group = group_var.get()
            self.presets[group].pop(index)
            self.presets[new_group].append({"name": new_name, "x": new_x, "y": new_y, "notes": new_notes})

            self.save_presets()
            self.refresh_group_display(group)
            if new_group != group:
                self.refresh_group_display(new_group)
            popup.destroy()

        btn_frame = ttk.Frame(pframe)
        btn_frame.grid(column=0, row=6, columnspan=3, pady=(10, 0))
        ttk.Button(btn_frame, text="Save", command=do_save).grid(column=0, row=0, padx=4)
        ttk.Button(btn_frame, text="Cancel", command=popup.destroy).grid(column=1, row=0, padx=4)

        self.center_popup(popup)

    def on_close(self):
        self.disconnect()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = LineApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
