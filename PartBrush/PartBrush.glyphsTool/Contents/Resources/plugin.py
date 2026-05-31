# encoding: utf-8
from __future__ import division, print_function, unicode_literals

import objc
import os
import traceback

from GlyphsApp import Glyphs, GSComponent, DOCUMENTACTIVATED, UPDATEINTERFACE
from GlyphsApp.plugins import SelectTool

from AppKit import (
    NSAffineTransform,
    NSBackingStoreBuffered,
    NSBezelStyleRegularSquare,
    NSButton,
    NSButtonTypeMomentaryChange,
    NSColor,
    NSCursor,
    NSFont,
    NSImage,
    NSImageOnly,
    NSLineBreakByTruncatingTail,
    NSOffState,
    NSOnState,
    NSPanel,
    NSRectFill,
    NSScrollView,
    NSTextField,
    NSView,
    NSViewHeightSizable,
    NSViewMaxYMargin,
    NSViewWidthSizable,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
    NSWindowStyleMaskUtilityWindow,
)

try:
    from AppKit import NSWindowStyleMaskNonactivatingPanel
except Exception:
    # NSNonactivatingPanelMask in older PyObjC builds.
    NSWindowStyleMaskNonactivatingPanel = 1 << 7

try:
    from AppKit import NSFloatingWindowLevel
except Exception:
    NSFloatingWindowLevel = 3
from Foundation import NSMakePoint, NSMakeRect, NSMakeSize

try:
    from AppKit import NSEdgeInsetsMake
except Exception:
    NSEdgeInsetsMake = None


class PartBrushFlippedGridView(NSView):
    def isFlipped(self):
        return True


class PartBrush(SelectTool):
    """Tool for placing _part.* / .part components by clicking in Edit View."""

    windowAutosaveName = "com.sur88.PartBrush.partsPalette.window"
    partPrefixes = ("_part.", ".part")
    cellSize = 72
    cellGap = 8
    padding = 14
    statusBarHeight = 34

    @objc.python_method
    def settings(self):
        self.name = Glyphs.localize({"en": "Part Brush"})
        icon_path = os.path.join(os.path.dirname(self.__file__()), "toolbarIconTemplate.pdf")
        icon = NSImage.alloc().initByReferencingFile_(icon_path)
        self._icon = None
        self.tool_bar_image = icon

        self.partBrushCursor = self.makePartBrushCursor()

        self.window = None
        self.scrollView = None
        self.gridView = None
        self.statusBar = None
        self.statusText = None
        self.partNames = []
        self.selectedPartName = None
        self.rawLocation = None
        self.lastLocation = None
        self.lastLayer = None
        self.lastGridSignature = None
        self.buttons = []

    @objc.python_method
    def start(self):
        try:
            Glyphs.addCallback(self.updateGridIfNeeded, DOCUMENTACTIVATED)
            Glyphs.addCallback(self.updateGridIfNeeded, UPDATEINTERFACE)
        except Exception:
            print(traceback.format_exc())
            Glyphs.showMacroWindow()

    @objc.python_method
    def activate(self):
        try:
            if self.window is None:
                self.buildWindow()
            self.updateGrid(force=True)
            # Keep the palette visible without taking keyboard/mouse focus away
            # from the Glyphs edit view. If the palette becomes key, the first
            # canvas click after choosing a part may be consumed just to return
            # focus to the document window.
            self.window.orderFrontRegardless()
            self.refocusEditView()
            self.forcePartBrushCursor()
            Glyphs.redraw()
        except Exception:
            print(traceback.format_exc())
            Glyphs.showMacroWindow()

    @objc.python_method
    def deactivate(self):
        self.rawLocation = None
        self.lastLocation = None
        self.lastLayer = None
        try:
            NSCursor.arrowCursor().set()
        except Exception:
            pass
        try:
            Glyphs.redraw()
        except Exception:
            pass

    @objc.python_method
    def makePartBrushCursor(self):
        """Create the cursor used while the Part Brush tool is active."""
        try:
            return NSCursor.crosshairCursor()
        except Exception:
            return None

    def standardCursor(self):
        """Glyphs asks tool plugins for their cursor through this method."""
        if self.partBrushCursor is not None:
            return self.partBrushCursor
        try:
            return NSCursor.crosshairCursor()
        except Exception:
            return None

    @objc.python_method
    def cursor(self):
        # Kept as a fallback for Glyphs/PyObjC builds that may call cursor().
        return self.standardCursor()

    @objc.python_method
    def forcePartBrushCursor(self):
        # Some Glyphs/PyObjC combinations do not refresh the cursor rects
        # immediately for Python tools. Setting the cursor during activation
        # and mouse events makes the active Part Brush state visible.
        try:
            cursor = self.standardCursor()
            if cursor is not None:
                cursor.set()
        except Exception:
            pass

    def mouseMoved_(self, theEvent):
        self.forcePartBrushCursor()
        self.updateMouseLocation(theEvent)

    def mouseDragged_(self, theEvent):
        self.forcePartBrushCursor()
        self.updateMouseLocation(theEvent)

    def mouseDown_(self, theEvent):
        try:
            self.forcePartBrushCursor()
            self.updateMouseLocation(theEvent)

            if self.selectedPartName is None:
                Glyphs.showNotification("Part Brush", "Choose a part in the Parts Palette first.")
                return

            font = Glyphs.font
            layer = self.activeLayer()
            if font is None or layer is None:
                Glyphs.showNotification("Part Brush", "Open a font and select a glyph layer first.")
                return

            glyph = layer.parent
            glyph.beginUndo()
            try:
                component = GSComponent(self.selectedPartName)
                if self.lastLocation is not None:
                    component.position = self.lastLocation
                layer.clearSelection()
                layer.shapes.append(component)
                component.selected = True
                Glyphs.redraw()
            finally:
                glyph.endUndo()
        except Exception:
            print(traceback.format_exc())
            Glyphs.showMacroWindow()

    @objc.python_method
    def updateMouseLocation(self, theEvent):
        try:
            graphicView = self.editViewController().graphicView()
            rawLocation = graphicView.getActiveLocation_(theEvent)
            snappedLocation = self.snapPointToGrid(rawLocation)
            self.rawLocation = rawLocation
            self.lastLayer = graphicView.activeLayer()

            shouldRedraw = True
            if self.lastLocation is not None and snappedLocation is not None:
                try:
                    shouldRedraw = (self.lastLocation.x != snappedLocation.x or self.lastLocation.y != snappedLocation.y)
                except Exception:
                    shouldRedraw = True
            self.lastLocation = snappedLocation
            if shouldRedraw:
                Glyphs.redraw()
        except Exception:
            self.rawLocation = None
            self.lastLocation = None
            self.lastLayer = None

    @objc.python_method
    def snapPointToGrid(self, point):
        if point is None:
            return None
        grid = self.currentGridSpacing()
        if grid is None or grid <= 0:
            return point
        try:
            x = round(float(point.x) / grid) * grid
            y = round(float(point.y) / grid) * grid
            return NSMakePoint(x, y)
        except Exception:
            return point

    @objc.python_method
    def currentGridSpacing(self):
        font = Glyphs.font
        if font is None:
            return 1.0
        for attr in ("gridMain", "gridLength", "gridSpacing"):
            try:
                value = getattr(font, attr)
                if value is not None and float(value) > 0:
                    return float(value)
            except Exception:
                pass
        return 1.0

    @objc.python_method
    def refocusEditView(self):
        """Keep the Glyphs edit view as first responder after palette actions."""
        try:
            graphicView = self.editViewController().graphicView()
            document = Glyphs.currentDocument
            if callable(document):
                document = document()
            if document is not None:
                wc = document.windowController()
                if wc is not None:
                    window = wc.window()
                    if window is not None:
                        try:
                            window.makeKeyWindow()
                        except Exception:
                            pass
                        try:
                            window.makeFirstResponder_(graphicView)
                        except Exception:
                            pass
        except Exception:
            pass

    @objc.python_method
    def activeLayer(self):
        try:
            return self.editViewController().graphicView().activeLayer()
        except Exception:
            try:
                font = Glyphs.font
                if font is not None and font.selectedLayers:
                    return font.selectedLayers[0]
            except Exception:
                pass
        return None

    @objc.python_method
    def foreground(self, layer):
        """Draw a translucent preview of the selected part at the cursor position."""
        try:
            if self.selectedPartName is None or self.lastLocation is None:
                return
            if self.lastLayer is not None and layer is not self.lastLayer:
                return

            font = Glyphs.font
            if font is None:
                return
            glyph = font.glyphs[self.selectedPartName]
            if glyph is None:
                return
            sourceLayer = self.previewLayerForGlyph(glyph, font)
            if sourceLayer is None:
                return

            closedPath = self.layerPath(sourceLayer, ["completeBezierPath", "drawBezierPath", "bezierPath"])
            openPath = self.layerPath(sourceLayer, ["completeOpenBezierPath", "drawOpenBezierPath", "openBezierPath"])
            if closedPath is None and openPath is None:
                return

            transform = NSAffineTransform.transform()
            transform.translateXBy_yBy_(self.lastLocation.x, self.lastLocation.y)

            NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.16).setFill()
            if closedPath is not None:
                p = closedPath.copy()
                p.transformUsingAffineTransform_(transform)
                p.fill()

            NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.55).setStroke()
            for path in (closedPath, openPath):
                if path is None:
                    continue
                p = path.copy()
                p.transformUsingAffineTransform_(transform)
                try:
                    p.setLineWidth_(1.0)
                except Exception:
                    pass
                p.stroke()
        except Exception:
            print(traceback.format_exc())

    @objc.python_method
    def buildWindow(self):
        mask = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskResizable
            | NSWindowStyleMaskUtilityWindow
        )
        initialWidth = 440
        initialHeight = 320
        self.window = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(320, 320, initialWidth, initialHeight), mask, NSBackingStoreBuffered, False
        )
        self.window.setTitle_("Parts Palette")
        self.window.setFloatingPanel_(True)
        self.window.setHidesOnDeactivate_(False)
        try:
            self.window.setLevel_(NSFloatingWindowLevel)
        except Exception:
            pass
        try:
            self.window.setBecomesKeyOnlyIfNeeded_(True)
        except Exception:
            pass
        try:
            self.window.setWorksWhenModal_(True)
        except Exception:
            pass
        self.window.setFrameAutosaveName_(self.windowAutosaveName)
        self.window.setMinSize_(NSMakeSize(260, 170))
        self.window.setDelegate_(self)

        content = self.window.contentView()
        content.setAutoresizesSubviews_(True)

        self.scrollView = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
        self.scrollView.setHasVerticalScroller_(True)
        self.scrollView.setHasHorizontalScroller_(False)
        self.scrollView.setAutohidesScrollers_(True)
        self.scrollView.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        try:
            self.scrollView.setBorderType_(0)
        except Exception:
            pass
        try:
            self.scrollView.setAutomaticallyAdjustsContentInsets_(False)
        except Exception:
            pass
        if NSEdgeInsetsMake is not None:
            try:
                self.scrollView.setContentInsets_(NSEdgeInsetsMake(0, 0, 0, 0))
                self.scrollView.setScrollerInsets_(NSEdgeInsetsMake(0, 0, 0, 0))
            except Exception:
                pass
        content.addSubview_(self.scrollView)

        self.statusBar = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, self.statusBarHeight))
        self.statusBar.setAutoresizingMask_(NSViewWidthSizable | NSViewMaxYMargin)
        self.statusBar.setAutoresizesSubviews_(True)
        content.addSubview_(self.statusBar)

        refreshButton = NSButton.alloc().initWithFrame_(NSMakeRect(12, 5, 76, 24))
        refreshButton.setTitle_("Refresh")
        refreshButton.setTarget_(self)
        refreshButton.setAction_("refresh:")
        self.statusBar.addSubview_(refreshButton)

        self.statusText = NSTextField.alloc().initWithFrame_(NSMakeRect(98, 8, initialWidth - 110, 18))
        self.statusText.setBezeled_(False)
        self.statusText.setDrawsBackground_(False)
        self.statusText.setEditable_(False)
        self.statusText.setSelectable_(False)
        self.statusText.setFont_(NSFont.systemFontOfSize_(11))
        try:
            self.statusText.cell().setUsesSingleLineMode_(True)
            self.statusText.cell().setLineBreakMode_(NSLineBreakByTruncatingTail)
        except Exception:
            pass
        self.statusText.setStringValue_("No font open")
        self.statusText.setAutoresizingMask_(NSViewWidthSizable)
        self.statusBar.addSubview_(self.statusText)

        self.layoutViews()

    def windowDidResize_(self, notification):
        try:
            if self.window is not None and self.window.isVisible():
                self.layoutViews()
                self.updateGrid(force=True)
        except Exception:
            print(traceback.format_exc())
            Glyphs.showMacroWindow()

    def refresh_(self, sender):
        self.updateGrid(force=True)

    def selectPart_(self, sender):
        try:
            index = sender.tag()
            if 0 <= index < len(self.partNames):
                self.selectedPartName = self.partNames[index]
                self.updateButtonStates()
                self.updateStatusText()
                self.refocusEditView()
                self.forcePartBrushCursor()
                Glyphs.redraw()
        except Exception:
            print(traceback.format_exc())
            Glyphs.showMacroWindow()

    @objc.python_method
    def layoutViews(self):
        if self.window is None or self.scrollView is None or self.statusBar is None:
            return
        content = self.window.contentView()
        bounds = content.bounds()
        width = bounds.size.width
        height = bounds.size.height
        scrollHeight = max(0, height - self.statusBarHeight)
        self.scrollView.setFrame_(NSMakeRect(0, self.statusBarHeight, width, scrollHeight))
        self.statusBar.setFrame_(NSMakeRect(0, 0, width, self.statusBarHeight))
        if self.statusText is not None:
            self.statusText.setFrame_(NSMakeRect(98, 8, max(20, width - 110), 18))

    @objc.python_method
    def updateGridIfNeeded(self, sender=None):
        if self.window is not None and self.window.isVisible():
            self.updateGrid(force=False)

    @objc.python_method
    def updateGrid(self, force=False):
        font = Glyphs.font
        if font is None:
            self.partNames = []
            self.selectedPartName = None
            self.lastGridSignature = None
            self.setStatus("No font open")
            self.populateGrid([])
            return

        masterId = font.selectedFontMaster.id if font.selectedFontMaster else ""
        width = 0
        height = 0
        if self.scrollView is not None:
            try:
                bounds = self.scrollView.contentView().bounds()
                width = int(bounds.size.width)
                height = int(bounds.size.height)
            except Exception:
                pass

        partNames = sorted([g.name for g in font.glyphs if self.isPartName(g.name)])
        signature = (font.familyName, masterId, width, height, tuple(partNames), self.selectedPartName)
        if not force and signature == self.lastGridSignature:
            return

        self.lastGridSignature = signature
        self.partNames = partNames
        if self.selectedPartName not in self.partNames:
            self.selectedPartName = self.partNames[0] if self.partNames else None
        self.updateStatusText()
        self.populateGrid(partNames)

    @objc.python_method
    def isPartName(self, name):
        return any(name.startswith(prefix) for prefix in self.partPrefixes)

    @objc.python_method
    def setStatus(self, text):
        if self.statusText is not None:
            self.statusText.setStringValue_(text)

    @objc.python_method
    def updateStatusText(self):
        font = Glyphs.font
        if font is None:
            self.setStatus("No font open")
            return
        selected = self.selectedPartName or "No part selected"
        self.setStatus("%d parts in %s — %s" % (len(self.partNames), font.familyName, selected))

    @objc.python_method
    def populateGrid(self, partNames):
        if self.scrollView is None:
            return

        clipBounds = self.scrollView.contentView().bounds()
        contentWidth = int(clipBounds.size.width)
        if contentWidth <= 0:
            contentWidth = 420

        visibleHeight = int(clipBounds.size.height)
        usableWidth = max(1, contentWidth - self.padding * 2)

        columns = max(1, int((usableWidth + self.cellGap) / (self.cellSize + self.cellGap)))
        while columns > 1:
            candidateSize = int((usableWidth - self.cellGap * (columns - 1) - 1) / columns)
            if candidateSize >= 36:
                break
            columns -= 1

        cellSize = int((usableWidth - self.cellGap * (columns - 1) - 1) / columns)
        cellSize = max(36, cellSize)

        rows = max(1, int((len(partNames) + columns - 1) / columns))
        contentHeight = self.padding * 2 + rows * cellSize + max(0, rows - 1) * self.cellGap
        contentHeight = max(contentHeight, visibleHeight)

        self.gridView = PartBrushFlippedGridView.alloc().initWithFrame_(NSMakeRect(0, 0, contentWidth, contentHeight))
        self.buttons = []

        for index, name in enumerate(partNames):
            col = index % columns
            row = index // columns
            x = self.padding + col * (cellSize + self.cellGap)
            y = self.padding + row * (cellSize + self.cellGap)

            button = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, cellSize, cellSize))
            button.setButtonType_(NSButtonTypeMomentaryChange)
            button.setBezelStyle_(NSBezelStyleRegularSquare)
            button.setImagePosition_(NSImageOnly)
            button.setImage_(self.imageForPart(name, max(18, cellSize - 14)))
            button.setTarget_(self)
            button.setAction_("selectPart:")
            button.setTag_(index)
            button.setToolTip_(name)
            if name == self.selectedPartName:
                button.setState_(NSOnState)
            else:
                button.setState_(NSOffState)
            self.buttons.append(button)
            self.gridView.addSubview_(button)

        self.scrollView.setDocumentView_(self.gridView)
        try:
            self.scrollView.contentView().scrollToPoint_(NSMakePoint(0, 0))
            self.scrollView.reflectScrolledClipView_(self.scrollView.contentView())
        except Exception:
            pass
        self.updateButtonStates()

    @objc.python_method
    def updateButtonStates(self):
        for index, button in enumerate(self.buttons):
            if index < len(self.partNames) and self.partNames[index] == self.selectedPartName:
                button.setState_(NSOnState)
            else:
                button.setState_(NSOffState)

    @objc.python_method
    def imageForPart(self, glyphName, size):
        image = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
        image.lockFocus()
        try:
            NSColor.clearColor().setFill()
            NSRectFill(NSMakeRect(0, 0, size, size))

            font = Glyphs.font
            if font is None:
                return image

            glyph = font.glyphs[glyphName]
            if glyph is None:
                return image
            layer = self.previewLayerForGlyph(glyph, font)
            if layer is None:
                return image

            closedPath = self.layerPath(layer, ["completeBezierPath", "drawBezierPath", "bezierPath"])
            openPath = self.layerPath(layer, ["completeOpenBezierPath", "drawOpenBezierPath", "openBezierPath"])
            bounds = self.unionBoundsForPaths([closedPath, openPath])
            if bounds is None or bounds.size.width == 0 or bounds.size.height == 0:
                return image

            margin = 6.0
            available = size - margin * 2.0
            scale = min(available / bounds.size.width, available / bounds.size.height)
            dx = margin + (available - bounds.size.width * scale) / 2.0
            dy = margin + (available - bounds.size.height * scale) / 2.0

            transform = NSAffineTransform.transform()
            transform.translateXBy_yBy_(dx, dy)
            transform.scaleBy_(scale)
            transform.translateXBy_yBy_(-bounds.origin.x, -bounds.origin.y)

            NSColor.colorWithCalibratedWhite_alpha_(0.18, 1.0).setFill()
            if closedPath is not None:
                p = closedPath.copy()
                p.transformUsingAffineTransform_(transform)
                p.fill()

            NSColor.colorWithCalibratedWhite_alpha_(0.18, 1.0).setStroke()
            if openPath is not None:
                p = openPath.copy()
                p.transformUsingAffineTransform_(transform)
                p.setLineWidth_(max(1.0, 1.2 / scale))
                p.stroke()
        finally:
            image.unlockFocus()
        return image

    @objc.python_method
    def previewLayerForGlyph(self, glyph, font):
        try:
            master = font.selectedFontMaster
            if master is not None:
                layer = glyph.layers[master.id]
                if layer is not None:
                    return layer
        except Exception:
            pass
        try:
            return glyph.layers[0]
        except Exception:
            return None

    @objc.python_method
    def layerPath(self, layer, attributeNames):
        for attr in attributeNames:
            try:
                path = getattr(layer, attr)
                if path is not None:
                    try:
                        if path.isEmpty():
                            continue
                    except Exception:
                        pass
                    return path
            except Exception:
                pass
        return None

    @objc.python_method
    def unionBoundsForPaths(self, paths):
        result = None
        for path in paths:
            if path is None:
                continue
            try:
                b = path.bounds()
            except Exception:
                continue
            if b.size.width == 0 and b.size.height == 0:
                continue
            if result is None:
                result = b
            else:
                x1 = min(result.origin.x, b.origin.x)
                y1 = min(result.origin.y, b.origin.y)
                x2 = max(result.origin.x + result.size.width, b.origin.x + b.size.width)
                y2 = max(result.origin.y + result.size.height, b.origin.y + b.size.height)
                result = NSMakeRect(x1, y1, x2 - x1, y2 - y1)
        return result

    @objc.python_method
    def __file__(self):
        """Please leave this method unchanged."""
        return __file__

    @objc.python_method
    def __del__(self):
        try:
            Glyphs.removeCallback(self.updateGridIfNeeded)
        except Exception:
            pass
