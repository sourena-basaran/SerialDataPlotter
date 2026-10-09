import os
import sys
import csv
import time
import shutil
import threading
from tempfile import gettempdir
import serial.tools.list_ports
import pyqtgraph as pg
import pyqtgraph.exporters
import numpy as np
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QPushButton, QComboBox, QFileDialog,
                               QRadioButton, QGroupBox, QFormLayout, QSpinBox,
                               QMessageBox)
from PySide6.QtCore import QTimer
    
class SerialPlotterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Real-Time Serial Data Plotter")
        self.resize(1200, 700)

        # Global Variables
        self.isRunning = False
        self.serial_port = None
        self.cubicSpline = None
        self.startTime = 0
        self.valCount = 0
        self.dataX = []
        self.dataY = []     # List of lists, holding the Y data of each line
        self.plotLines = []
        self.isUnsaved = False
        self.hasAutoScaled = False
        # This creates a path like: C:\Users\name\AppData\Local\Temp\dataPlotter_backup.csv
        self.temp_filename = os.path.join(gettempdir(), "dataPlotter_backup.csv")
        self.backup_handle = None
        self.backup_writer = None
        # Threading Variables
        self.dataLock = threading.Lock()
        self.dataThread = None      # This thread will do the data acquisition independently of GUI

        self.colors = [
            "#E41A1C", "#377EB8", "#4DAF4A", "#984EA3", "#FF7F00", "#F0E442", 
            "#A65628", "#F781BF", "#999999", "#00B0F6", "#FC8D62", "#8DA0CB", 
            "#E78AC3", "#A6D854", "#D55E00", "#009E73", "#CC79A7", "#0072B2", 
            "#FFB5B8", "#1CE6FF", "#FF34FF", "#008941", "#A30059", "#FF4A46", 
            "#006FA6", "#B79762", "#004D43", "#8FB0FF", "#997D87", "#E20027"
        ]

        # Since the cold launch is slow, we import the scipy library in parallel
        threading.Thread(target=self._background_imports, daemon=True).start()
        self.init_ui()
        self.applyTheme()
        self.checkForCrashRecovery()

        self.timer = QTimer()
        self.timer.timeout.connect(self.drawGraphs)
        
    def _background_imports(self):
        try:
            from scipy.interpolate import CubicSpline
            self.cubicSpline = CubicSpline
        except ImportError:
            pass
        
    def init_ui(self):
        # Main Layout
        centralWidget = QWidget()
        self.setCentralWidget(centralWidget)
        mainLayout = QHBoxLayout(centralWidget)
        mainLayout.setContentsMargins(15, 15, 15, 15)
        mainLayout.setSpacing(25)

        # --- LEFT PANEL (Controls) ---
        controlLayout = QVBoxLayout()
        controlLayout.setSpacing(15)
        
        # Connection Settings
        connectionGroup = QGroupBox("Connection Settings")
        connectionForm = QFormLayout()

        portLayout = QHBoxLayout()
        self.portCombo = QComboBox()
        self.findPorts() # Populates the combo initially
        self.btn_refresh = QPushButton("↻")
        self.btn_refresh.setFixedWidth(30)
        self.btn_refresh.clicked.connect(self.findPorts)
        portLayout.addWidget(self.portCombo)
        portLayout.addWidget(self.btn_refresh)
        connectionForm.addRow("Port:", portLayout)
        
        self.baudCombo = QComboBox()
        self.baudCombo.addItems(["9600", "19200", "38400", "57600", "115200", "250000", "500000", "1000000", "2000000"])
        connectionForm.addRow("Baudrate:", self.baudCombo)
        
        self.fpsCombo = QComboBox()
        self.fpsCombo.addItems([
            "10 FPS (Eco Mode)", 
            "20 FPS (Standard)", 
            "30 FPS (Smooth)", 
            "60 FPS (Extreme)"
        ])
        self.fpsCombo.setCurrentIndex(1) # Default to 20 FPS
        self.fpsCombo.currentIndexChanged.connect(self.changeFPS)
        connectionForm.addRow("FPS:", self.fpsCombo)
        
        connectionGroup.setLayout(connectionForm)
        controlLayout.addWidget(connectionGroup)
        
        # Action Buttons
        self.btn_start = QPushButton("Start Plotting")
        self.btn_start.setMinimumHeight(30)
        self.btn_start.clicked.connect(self.startPlotting)
        controlLayout.addWidget(self.btn_start)
        
        self.btn_stop = QPushButton("Stop Plotting")
        self.btn_stop.setMinimumHeight(30)
        self.btn_stop.clicked.connect(self.stopPlotting)
        self.btn_stop.setEnabled(False)
        controlLayout.addWidget(self.btn_stop)

        # Graph View Controls
        self.btn_reset = QPushButton("Reset View (Auto-Scale)")
        self.btn_reset.setMinimumHeight(30)
        self.btn_reset.clicked.connect(self.resetGraphView)
        controlLayout.addWidget(self.btn_reset)

        self.btn_import = QPushButton("Import CSV")
        self.btn_import.clicked.connect(self.importData)
        controlLayout.addWidget(self.btn_import)
        
        self.btn_export = QPushButton("Export CSV / Image")
        self.btn_export.clicked.connect(self.exportData)
        controlLayout.addWidget(self.btn_export)

        self.btn_theme = QPushButton("Toggle White Graph (Poster Mode)")
        self.btn_theme.clicked.connect(self.toggleGraphTheme)
        controlLayout.addWidget(self.btn_theme)
        
        # Graph Options
        graphGroup = QGroupBox("Graph Type")
        graphVBox = QVBoxLayout()
        self.radioLinear = QRadioButton("Linear")

        polyLayout = QHBoxLayout()
        self.radioPoly = QRadioButton("Polynomial (Trendline)")
        self.polySpin = QSpinBox()
        self.polySpin.setRange(2, 6)
        self.polySpin.setValue(3)
        self.polySpin.setToolTip("Polynomial Degree")
        self.polySpin.setMinimumHeight(25)
        self.polySpin.valueChanged.connect(self.drawGraphs)
        polyLayout.addWidget(self.radioPoly)
        polyLayout.addWidget(self.polySpin)
        polyLayout.addStretch()
        
        self.radioSpline = QRadioButton("Spline (Smooth Line)")
        self.radioLog = QRadioButton("Logarithmic")
        self.radioLog.setChecked(True)
        self.radioExp = QRadioButton("Exponential")
        self.radioPow = QRadioButton("Power")

        avgLayout = QHBoxLayout()
        self.radioAvg = QRadioButton("Moving Average")
        self.avgSpin = QSpinBox()
        self.avgSpin.setRange(2, 50)
        self.avgSpin.setValue(5)
        self.avgSpin.setToolTip("Window Period")
        self.avgSpin.setMinimumHeight(25)
        self.avgSpin.valueChanged.connect(self.drawGraphs)
        avgLayout.addWidget(self.radioAvg)
        avgLayout.addWidget(self.avgSpin)
        avgLayout.addStretch()

        self.radioLinear.toggled.connect(self.drawGraphs)
        self.radioPoly.toggled.connect(self.drawGraphs)
        self.radioSpline.toggled.connect(self.drawGraphs)
        self.radioLog.toggled.connect(self.drawGraphs)
        self.radioExp.toggled.connect(self.drawGraphs)
        self.radioPow.toggled.connect(self.drawGraphs)
        self.radioAvg.toggled.connect(self.drawGraphs)

        graphVBox.addWidget(self.radioLinear)
        graphVBox.addLayout(polyLayout)
        graphVBox.addWidget(self.radioSpline)
        graphVBox.addWidget(self.radioLog)
        graphVBox.addWidget(self.radioExp)
        graphVBox.addWidget(self.radioPow)
        graphVBox.addLayout(avgLayout)

        graphGroup.setLayout(graphVBox)
        controlLayout.addWidget(graphGroup)
        
        controlLayout.addStretch()      # Pushes everything to the top
        mainLayout.addLayout(controlLayout, 1)
        
        # --- RIGHT PANEL (Graph) ---
        self.plotWidget = pg.PlotWidget()
        self.plotWidget.showGrid(x=True, y=True, alpha=0.3)
        legend = self.plotWidget.addLegend(offset=(20, 20))
        legend.setBrush(pg.mkBrush(color=(30, 30, 30, 200)))
        self.plotWidget.setLimits(xMin=0)       # Prevents going into negatives
        mainLayout.addWidget(self.plotWidget, 4)

    def applyTheme(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #1E1E1E; }
            QLabel, QRadioButton { color: #E0E0E0; font-weight: bold; }
            QGroupBox { 
                color: #E0E0E0; 
                font-weight: bold; 
                background-color: #252525;
                border: 1px solid #888888; 
                border-radius: 6px;
                margin-top: 12px; 
                padding-top: 15px; 
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px 0 3px; }
            QPushButton { background-color: #333333; color: #E0E0E0; border: 1px solid #666; padding: 5px; border-radius: 4px; }
            QPushButton:hover { background-color: #444444; border: 1px solid #888; }
            QPushButton:disabled { background-color: #222222; color: #666666; border: 1px solid #444; }
            QComboBox { background-color: #3A3A3A; color: #E0E0E0; border: 1px solid #666; padding: 4px; border-radius: 4px; }
            QComboBox:hover { background-color: #4A4A4A; border: 1px solid #888; }
        """)
        self.plotWidget.setBackground('#121212')
        self.plotWidget.setTitle("Real-Time Sensor Data over Time", color="#E0E0E0", size="15pt")
        self.plotWidget.setLabel("left", "Sensor Value", color="#CCCCCC", size="12pt")
        self.plotWidget.setLabel("bottom", "Time (Seconds)", color="#CCCCCC", size="12pt")

    def checkForCrashRecovery(self):
        """Checks if a backup file exists from a previous crashed session."""
        if os.path.exists(self.temp_filename):
            # Makes sure the file actually has data in it (more than 0 bytes)
            if os.path.getsize(self.temp_filename) > 10:
                reply = QMessageBox.question(
                    self, 'Restore Session',
                    'An unsaved session or active workspace from your last visit was found.\n\nWould you like to restore this data to the graph?',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
                )
                if reply == QMessageBox.Yes:
                    self.recoverData(self.temp_filename)
                    self.isUnsaved = True
                else:
                    # If they don't want it, delete it so we don't ask again next time
                    try: os.remove(self.temp_filename)
                    except: pass
            else:
                # File is empty, just clean it up
                try: os.remove(self.temp_filename)
                except: pass

    def recoverData(self, file_path):
        """Silently loads the backup data without opening a file dialog."""
        try:
            with open(file_path, 'r') as file:
                reader = csv.reader(file)
                headers = next(reader)
                self.valCount = len(headers) - 1 
                self.dataX = []
                self.dataY = [[] for _ in range(self.valCount)]
                self.plotLines = []
                self.plotWidget.clear()
                for i in range(self.valCount):
                    color = self.colors[i % len(self.colors)]
                    pen = pg.mkPen(color=color, width=2) 
                    line_plot = self.plotWidget.plot([], [], name=f"Sensor {i+1}", pen=pen, antialias=True)
                    self.plotLines.append(line_plot)
                for row in reader:
                    if len(row) != self.valCount + 1: continue
                    self.dataX.append(float(row[0]))
                    for i in range(self.valCount):
                        self.dataY[i].append(float(row[i+1]))
                        
            self.drawGraphs()
            self.resetGraphView()
            QMessageBox.information(self, "Recovery Successful", f"Recovered {len(self.dataX)} rows of data.\n\nPlease Export this data now to save it permanently.")
            
        except Exception as e:
            QMessageBox.critical(self, "Recovery Error", f"Could not read the backup file:\n{str(e)}")
            
    def findPorts(self):
        currentPort = self.portCombo.currentText()
        self.portCombo.clear()
        ports = serial.tools.list_ports.comports()
        for port in ports:
            self.portCombo.addItem(port.device)

        if self.portCombo.count() == 0:
            self.portCombo.addItem("No Ports Found")
        else:
            # Put the selected port back to what was selected
            index = self.portCombo.findText(currentPort)
            if index >= 0:
                self.portCombo.setCurrentIndex(index)

    def changeFPS(self):
        fps_text = self.fpsCombo.currentText()
        # Convert FPS to milliseconds (1000ms / FPS)
        if "10" in fps_text: ms = 100
        elif "20" in fps_text: ms = 50
        elif "30" in fps_text: ms = 33
        elif "60" in fps_text: ms = 16
        else: ms = 50
        # If the plot is currently running, apply the new speed instantly
        self.timer.setInterval(ms)
            
    def importData(self):
        if self.isUnsaved:
            reply = QMessageBox.question(
                self, 'Unsaved Data',
                'You have live data that has not been exported yet. Importing a new file will erase your current graph.\n\nAre you sure you want to proceed?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.No:
                return
        file_path, _ = QFileDialog.getOpenFileName(self, "Import CSV", "", "CSV Files (*.csv)")
        if not file_path: return
        try:
            with open(file_path, 'r') as file:
                reader = csv.reader(file)
                headers = next(reader)
                # Figure out how many sensors there are based on the columns (-1 for Time)
                self.valCount = len(headers) - 1 
                self.dataX = []
                self.dataY = [[] for _ in range(self.valCount)]
                # Rebuild the plot lines if necessary
                self.plotLines = []
                self.plotWidget.clear()
                for i in range(self.valCount):
                    color = self.colors[i % len(self.colors)]
                    pen = pg.mkPen(color=color, width=2) 
                    line_plot = self.plotWidget.plot([], [], name=f"Sensor {i+1}", pen=pen, antialias=True)
                    self.plotLines.append(line_plot)
                # Read all the data
                for row in reader:
                    if len(row) != self.valCount + 1: continue
                    self.dataX.append(float(row[0]))
                    for i in range(self.valCount):
                        self.dataY[i].append(float(row[i+1]))

            # Copy the imported file to the temp backup so they can re-export it if needed
            shutil.copy(file_path, self.temp_filename)
            self.drawGraphs()
            self.resetGraphView()
            self.isUnsaved = False
            QMessageBox.information(self, "Import Successful", f"Loaded {len(self.dataX)} rows of data.")
        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Could not read the CSV file:\n{str(e)}")
            
    def exportData(self):
        if not self.dataX or self.valCount == 0:
            QMessageBox.warning(self, "No Data", "There is no data to export yet!\n\nPlease click 'Start Plotting' first.")
            return
        wasRunning = self.timer.isActive()
        if wasRunning: self.timer.stop()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Graph Data",
            "",   # Opens in default user directory
            "CSV Files (*.csv);;PNG Image (*.png);;JPEG Image (*.jpg)"
        )
        if not path:
            if wasRunning: self.timer.start()
            return      # User clicked cancel
        try:
            if path.endswith(".csv"):
                with open(path, mode='w', newline='') as file:
                    writer = csv.writer(file)
                    headers = ["Time (s)"] + [f"Sensor {i+1}" for i in range(self.valCount)]
                    writer.writerow(headers)
                    for i in range(len(self.dataX)):
                        row = [self.dataX[i]]
                        for j in range(self.valCount):
                            row.append(self.dataY[j][i])
                        writer.writerow(row)
                    if not self.isRunning:
                        if os.path.exists(self.temp_filename):
                            try: os.remove(self.temp_filename)
                            except: pass
                    self.isUnsaved = False
            elif path.endswith(".png") or path.endswith(".jpg"):
                exporter = pg.exporters.ImageExporter(self.plotWidget.plotItem)
                exporter.export(path)
        except PermissionError:
            QMessageBox.critical(self, "Permission Denied", "Could not save the file.\n\nIf you are exporting a CSV, make sure it is not currently open in another program!")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"An unexpected error occured:\n{str(e)}")
        finally:
            if wasRunning: self.timer.start()

    def toggleGraphTheme(self):
        # Check current color and flip it
        if self.plotWidget.backgroundBrush().color().name() == '#121212':
            self.plotWidget.setBackground('w')
            # Make text and axes black for the white background
            self.plotWidget.getAxis('left').setPen('k')
            self.plotWidget.getAxis('left').setTextPen('k')
            self.plotWidget.getAxis('bottom').setPen('k')
            self.plotWidget.getAxis('bottom').setTextPen('k')
        else:
            self.plotWidget.setBackground('#121212')
            # Revert to standard dark theme colors
            self.plotWidget.getAxis('left').setPen('#CCCCCC')
            self.plotWidget.getAxis('left').setTextPen('#CCCCCC')
            self.plotWidget.getAxis('bottom').setPen('#CCCCCC')
            self.plotWidget.getAxis('bottom').setTextPen('#CCCCCC')
            
    def startPlotting(self):
        if self.isUnsaved:
            reply = QMessageBox.question(
                self, 'Unsaved Data',
                'You have un-exported data on the graph. Starting a new session will erase it.\n\nAre you sure you want to start a new plot?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.No:
                return
        try:
            port = self.portCombo.currentText()
            baud = int(self.baudCombo.currentText())
            self.serial_port = serial.Serial(port, baud, timeout=0.1) 
        except Exception as e:
            QMessageBox.critical(self, "Connection Error", f"Could not connect to {port}:\n\n{str(e)}")
            return
        
        self.isRunning = True
        self.hasAutoScaled = False
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_import.setEnabled(False)

        # Reset Data
        self.dataX = []
        self.dataY = []
        self.plotLines = []
        self.valCount = 0
        self.plotWidget.clear()
        self.resetGraphView()

        self.backup_handle = open(self.temp_filename, mode='w', newline='')
        self.backup_writer = csv.writer(self.backup_handle)
        
        # Get the starting speed based on whatever is selected
        self.changeFPS() 
        self.startTime = time.time()

        # Threading Stuff
        self.dataThread = threading.Thread(target=self.readSerialData, daemon=True)
        self.dataThread.start()

        # Start the timer using the dynamically set interval
        self.timer.timeout.disconnect()
        self.timer.timeout.connect(self.drawGraphs)
        self.timer.start()

    def stopPlotting(self):
        self.isRunning = False
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_import.setEnabled(True)
        self.timer.stop()
        # Safely release the serial port
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
        if self.backup_handle:
            self.backup_handle.close()
            self.backup_handle = None

    def resetGraphView(self):
        with self.dataLock:
            if self.valCount == 0 or len(self.dataX) < 2:
                self.plotWidget.enableAutoRange(axis='xy')
                return
            minX = self.dataX[0]
            maxX = self.dataX[-1]

        xPadding = (maxX - minX) * 0.05
        self.plotWidget.setXRange(minX - xPadding, maxX + xPadding, padding=0)
        self.plotWidget.enableAutoRange(axis='xy')
        if self.plotWidget.plotItem.legend is not None:
            self.plotWidget.plotItem.legend.setPos(20, 20)

    def readSerialData(self):
        writerCount = 0
        while self.isRunning and self.serial_port and self.serial_port.is_open: 
            try:
                # Read ALL waiting lines in the buffer to prevent graph lag
                if self.serial_port.in_waiting > 0:
                    raw_bytes = self.serial_port.readline()
                    try:
                        # If it's garbage, this decode will fail safely
                        dataLine = raw_bytes.decode('utf-8').strip()
                    except UnicodeDecodeError:
                        continue        # Skip the garbage byte and try the next line
                    if not dataLine: continue
                    if not all(c.isdigit() or c in ' .,-' for c in dataLine): continue      
                    values = [float(v) for v in dataLine.split(',')]
                    if self.valCount == 0:
                        tempValCount = len(values)
                        headers = ["Time (s)"] + [f"Sensor {i+1}" for i in range(tempValCount)]
                        self.backup_writer.writerow(headers)
                        with self.dataLock:
                            self.valCount = tempValCount
                            self.dataY = [[] for _ in range(self.valCount)]
                    
                    if len(values) != self.valCount: continue
                    currentTime = time.time() - self.startTime
                    self.backup_writer.writerow([currentTime] + values)
                    with self.dataLock:
                        self.dataX.append(currentTime)
                        for i in range(self.valCount):
                            self.dataY[i].append(values[i])

                    self.isUnsaved = True
                    # Flush the backup writes above to csv every 20 frames
                    if writerCount >= 20:
                        self.backup_handle.flush()
                        writerCount = -1
                    writerCount += 1
            except Exception:
                # Ignore random decode errors during serial transmission
                pass    

    def drawGraphs(self):
        # Copies the data to local variables so we don't block the readSerialData's thread while working
        with self.dataLock:
            currValCount = self.valCount
            if currValCount == 0 or len(self.dataX) < 2:
                return
            rawX = np.array(self.dataX)
            rawYList = [np.array(self.dataY[i]) for i in range(currValCount)]

        # If the background thread just started and the GUI hasn't created the lines yet
        if len(self.plotLines) == 0:
            for i in range(currValCount):
                color = self.colors[i % len(self.colors)]
                pen = pg.mkPen(color=color, width=2) 
                line = self.plotWidget.plot([], [], name=f"Sensor {i+1}", pen=pen, antialias=True, autoDownsample=True, clipToView=True)
                self.plotLines.append(line)

        if not self.hasAutoScaled:
            self.resetGraphView()
            self.hasAutoScaled = True
            
        # If it's linear we'll just handle it here and exit early
        if self.radioLinear.isChecked():
            for i in range(currValCount):
                self.plotLines[i].setData(rawX, rawYList[i])
            return
        
        minX, maxX = self.plotWidget.viewRange()[0]
        padding = max((maxX - minX) * 0.5, 5.0)   # We'll evaluate for half a screen more values both after and before
        viewMask = (rawX >= (minX - padding)) & (rawX <= (maxX + padding))
        visibleX = rawX[viewMask]
        if len(visibleX) < 2:
            return

        # The idea is to calculate the functions using their full values (rawX and rawY) however drawing only visibleX and visibleY
        # Though since cubic spline's values are not dependent on previous or future values which are not visible, we dont need to worry about them
        for i in range(self.valCount):
            rawY = rawYList[i]
            visibleY = rawY[viewMask]
            plotX = visibleX
            plotY = visibleY
            try:
                polyDegree = self.polySpin.value()
                if self.radioPoly.isChecked() and len(visibleX) > polyDegree:
                    # Poynomial Trendline (1 Equation for the whole graph)
                    # y = ax^3 + bx^2 + cx + d
                    coeffs = np.polyfit(rawX, rawY, polyDegree)
                    plotY = np.polyval(coeffs, visibleX)
                elif self.radioSpline.isChecked() and len(visibleX) > 3:
                    # Cubic Spline (Piecewise equations between every dot)
                    uniqueX, uniqueIndices = np.unique(visibleX, return_index=True)
                    uniqueY = visibleY[uniqueIndices]
                    if self.cubicSpline is not None and len(uniqueX) > 3:
                        cs = self.cubicSpline(uniqueX, uniqueY)
                        plotX = np.linspace(uniqueX.min(), uniqueX.max(), 300)
                        plotY = cs(plotX)
                elif self.radioLog.isChecked():
                    # y = m*ln(x) + b -> let u = lnx() -> y = mu + b
                    # log(0) is impossible, so we ensure the minimum X is 0.0000001
                    # Calculate the full real coefficients
                    safeXGlobal = np.maximum(rawX, 0.0000001)
                    coeffs = np.polyfit(np.log(safeXGlobal), rawY, 1)
                    # Apply coefficients only to the visible segment
                    safeXLocal = np.maximum(visibleX, 0.0000001)
                    plotY = coeffs[0] * np.log(safeXLocal) + coeffs[1]
                elif self.radioExp.isChecked():
                    # y = a * e^(bx) -> lny = lna + bx
                    # let Y = lny, A = lna -> Y = bx + A
                    safeY = np.maximum(rawY, 0.0000001)
                    coeffs = np.polyfit(rawX, np.log(safeY), 1)
                    b = coeffs[0]
                    a = np.exp(coeffs[1])
                    plotY = a * np.exp(b * visibleX)
                elif self.radioPow.isChecked():
                    # y = a * x^b -> lny = lna + blnx
                    # let Y = lny, A = lna, X = lnx -> Y = bX + A
                    safeXGlobal = np.maximum(rawX, 0.0000001)
                    safeY = np.maximum(rawY, 0.0000001)
                    coeffs = np.polyfit(np.log(safeXGlobal), np.log(safeY), 1)
                    b = coeffs[0]
                    a = np.exp(coeffs[1])
                    safeXLocal = np.maximum(visibleX, 0.0000001)
                    plotY = a * (safeXLocal ** b)
                elif self.radioAvg.isChecked():
                    avgWindow = self.avgSpin.value()
                    if len(rawX) >= avgWindow:
                        fullPlotY = np.convolve(rawY, np.ones(avgWindow)/avgWindow, mode="valid")
                        fullPlotX = rawX[avgWindow - 1:]
                        avgMask = (fullPlotX >= (minX - padding)) & (fullPlotX <= (maxX + padding))
                        plotY = fullPlotY[avgMask]
                        plotX = fullPlotX[avgMask]
                # Safe fallback to linear in case an error occurs    
                self.plotLines[i].setData(plotX, plotY)
                
            except Exception as e:
                # If math fails we'll just draw linearly
                print(f"Math Error on Graph {i}: {e}")
                self.plotLines[i].setData(visibleX, visibleY)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SerialPlotterApp()
    window.show()
    sys.exit(app.exec())
