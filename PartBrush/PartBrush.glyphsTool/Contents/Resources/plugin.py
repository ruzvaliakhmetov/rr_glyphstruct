# encoding: utf-8
from __future__ import division, print_function, unicode_literals

import objc
import os
import time
import traceback

from GlyphsApp import Glyphs, GSComponent, DOCUMENTACTIVATED, UPDATEINTERFACE
from GlyphsApp.plugins import SelectTool

from AppKit import (
    NSAffineTransform,
    NSBackingStoreBuffered,
    NSBezierPath,
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

try:
    from AppKit import NSEventModifierFlagOption
except Exception:
    # AppKit's Option/Alt key bit. Older PyObjC builds may only expose NSAlternateKeyMask.
    NSEventModifierFlagOption = 1 << 19

try:
    from AppKit import NSAlternateKeyMask
except Exception:
    NSAlternateKeyMask = NSEventModifierFlagOption

try:
    from AppKit import NSEventModifierFlagCommand
except Exception:
    # AppKit's Command key bit. Older PyObjC builds may only expose NSCommandKeyMask.
    NSEventModifierFlagCommand = 1 << 20

try:
    from AppKit import NSCommandKeyMask
except Exception:
    NSCommandKeyMask = NSEventModifierFlagCommand
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

    windowAutosaveName = "com.PartBrush.partsPalette.window"
    partPrefixes = ("_part.", ".part")
    cellSize = 72
    cellGap = 8
    padding = 14
    statusBarHeight = 34

    # Glyphs can instantiate Python tool plugins more than once while documents
    # are opened/closed. The palette must therefore live on the class, not on a
    # single tool instance, otherwise every new instance creates another panel.
    _sharedWindow = None
    _sharedScrollView = None
    _sharedGridView = None
    _sharedStatusBar = None
    _sharedStatusText = None
    _sharedRefreshButton = None
    _sharedButtons = []
    _activePaletteOwner = None
    _callbacksRegistered = False
    _callbackOwner = None

    @objc.python_method
    def settings(self):
        self.name = Glyphs.localize({"en": "Part Brush"})
        resources_path = os.path.dirname(self.__file__())
        icon_path = os.path.join(resources_path, "toolbarIconTemplate.pdf")
        icon = NSImage.alloc().initByReferencingFile_(icon_path)
        self._icon = icon
        self.tool_bar_image = icon

        self.partBrushCursor = self.makePartBrushCursor()

        self.window = None
        self.scrollView = None
        self.gridView = None
        self.statusBar = None
        self.statusText = None
        self.refreshButton = None
        self.partNames = []
        self.selectedPartName = None
        self.previewReferenceSize = None
        self.rawLocation = None
        self.lastLocation = None
        self.lastLayer = None
        self.lastGridSignature = None
        self.buttons = []
        self.toolActive = False
        self.optionKeyDown = False
        self.commandKeyDown = False
        self.temporarySelectMode = False
        self.paintingMode = False
        self.paintingLayer = None
        self.paintingGlyph = None
        self.paintingPartName = None
        self.paintingStepSize = None
        self.paintAnchorPoint = None
        self.lastPaintCell = None
        self.paintedCells = set()
        self.paintingUndoOpen = False
        self.paintingDidDrag = False
        self.paintStrokeComponents = []
        self.lastPassiveGridCheck = 0.0

    @objc.python_method
    def start(self):
        try:
            # Glyphs can create several instances of a Python tool while fonts
            # are opened/closed. Keep global callbacks singleton as well, or
            # stale instances will keep doing duplicate UPDATEINTERFACE checks.
            if not PartBrush._callbacksRegistered:
                Glyphs.addCallback(self.updateGridIfNeeded, DOCUMENTACTIVATED)
                Glyphs.addCallback(self.updateGridIfNeeded, UPDATEINTERFACE)
                PartBrush._callbacksRegistered = True
                PartBrush._callbackOwner = self
        except Exception:
            print(traceback.format_exc())
            Glyphs.showMacroWindow()

    @objc.python_method
    def activate(self):
        try:
            self.toolActive = True
            PartBrush._activePaletteOwner = self
            self.adoptSharedPalette()
            if self.window is None:
                self.buildWindow()
            else:
                self.retargetPaletteControls()
            self.updateGrid(force=True)
            self.updateButtonStates()
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
        self.finishPainting()
        self.toolActive = False
        self.rawLocation = None
        self.lastLocation = None
        self.lastLayer = None
        self.optionKeyDown = False
        self.commandKeyDown = False
        self.temporarySelectMode = False
        self.resetPaintingState()
        if PartBrush._activePaletteOwner is self:
            PartBrush._activePaletteOwner = None
        try:
            self.updateButtonStates()
        except Exception:
            pass
        try:
            NSCursor.arrowCursor().set()
        except Exception:
            pass
        try:
            Glyphs.redraw()
        except Exception:
            pass

    @objc.python_method
    def adoptSharedPalette(self):
        """Attach this tool instance to the one shared Parts Palette, if it exists."""
        self.window = PartBrush._sharedWindow
        self.scrollView = PartBrush._sharedScrollView
        self.gridView = PartBrush._sharedGridView
        self.statusBar = PartBrush._sharedStatusBar
        self.statusText = PartBrush._sharedStatusText
        self.refreshButton = PartBrush._sharedRefreshButton
        try:
            self.buttons = list(PartBrush._sharedButtons or [])
        except Exception:
            self.buttons = []
        self.retargetPaletteControls()

    @objc.python_method
    def storeSharedPalette(self):
        """Remember the current palette controls so later tool instances reuse them."""
        PartBrush._sharedWindow = self.window
        PartBrush._sharedScrollView = self.scrollView
        PartBrush._sharedGridView = self.gridView
        PartBrush._sharedStatusBar = self.statusBar
        PartBrush._sharedStatusText = self.statusText
        PartBrush._sharedRefreshButton = self.refreshButton
        try:
            PartBrush._sharedButtons = list(self.buttons or [])
        except Exception:
            PartBrush._sharedButtons = []

    @objc.python_method
    def clearSharedPalette(self):
        """Forget the shared palette after the user closes the panel."""
        if PartBrush._sharedWindow is self.window:
            PartBrush._sharedWindow = None
            PartBrush._sharedScrollView = None
            PartBrush._sharedGridView = None
            PartBrush._sharedStatusBar = None
            PartBrush._sharedStatusText = None
            PartBrush._sharedRefreshButton = None
            PartBrush._sharedButtons = []
        self.window = None
        self.scrollView = None
        self.gridView = None
        self.statusBar = None
        self.statusText = None
        self.refreshButton = None
        self.buttons = []
        self.lastGridSignature = None

    @objc.python_method
    def retargetPaletteControls(self):
        """Make the shared window delegate/buttons point at the current tool instance."""
        try:
            if self.window is not None:
                self.window.setDelegate_(self)
        except Exception:
            pass
        try:
            if self.refreshButton is not None:
                self.refreshButton.setTarget_(self)
        except Exception:
            pass
        for button in getattr(self, "buttons", []) or []:
            try:
                button.setTarget_(self)
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
        if self.isSelectModifierDown() or getattr(self, "temporarySelectMode", False):
            return self.selectionToolCursor()
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
    def selectionToolCursor(self):
        """Fast Selection Tool cursor used while Option/Alt or Command is held."""
        # Calling SelectTool.standardCursor() on every cursor request can be
        # surprisingly expensive during drag. The built-in selection cursor is
        # effectively the arrow for this temporary pass-through mode, so return
        # it directly and keep the mouse hot path light.
        try:
            return NSCursor.arrowCursor()
        except Exception:
            return None

    @objc.python_method
    def isOptionKeyPressed(self, theEvent):
        try:
            flags = int(theEvent.modifierFlags())
        except Exception:
            return False
        try:
            optionMask = int(NSEventModifierFlagOption) | int(NSAlternateKeyMask)
        except Exception:
            optionMask = 1 << 19
        return bool(flags & optionMask)

    @objc.python_method
    def isCommandKeyPressed(self, theEvent):
        try:
            flags = int(theEvent.modifierFlags())
        except Exception:
            return False
        try:
            commandMask = int(NSEventModifierFlagCommand) | int(NSCommandKeyMask)
        except Exception:
            commandMask = 1 << 20
        return bool(flags & commandMask)

    @objc.python_method
    def isSelectModifierDown(self):
        return bool(getattr(self, "optionKeyDown", False) or getattr(self, "commandKeyDown", False))

    @objc.python_method
    def clearPreviewLocation(self):
        self.rawLocation = None
        self.lastLocation = None
        self.lastLayer = None

    @objc.python_method
    def resetPaintingState(self):
        self.paintingMode = False
        self.paintingLayer = None
        self.paintingGlyph = None
        self.paintingPartName = None
        self.paintingStepSize = None
        self.paintAnchorPoint = None
        self.lastPaintCell = None
        self.paintedCells = set()
        self.paintingUndoOpen = False
        self.paintingDidDrag = False
        self.paintStrokeComponents = []

    @objc.python_method
    def finishPainting(self, theEvent=None):
        """Close a Part Brush drag stroke and restore the normal preview state."""
        glyph = getattr(self, "paintingGlyph", None)
        undoOpen = getattr(self, "paintingUndoOpen", False)
        try:
            if undoOpen and glyph is not None:
                try:
                    glyph.endUndo()
                except Exception:
                    pass
        finally:
            wasPainting = getattr(self, "paintingMode", False)
            self.resetPaintingState()
            if theEvent is not None and wasPainting and not self.isSelectModifierDown():
                self.updateMouseLocation(theEvent)
            try:
                Glyphs.redraw()
            except Exception:
                pass

    @objc.python_method
    def selectedPartStepSize(self):
        """Return the X/Y repeat step for drag-painting the selected part."""
        fallback = self.currentGridSpacing()
        if fallback is None or fallback <= 0:
            fallback = 1.0

        xStep = fallback
        yStep = fallback
        font = Glyphs.font
        if font is None or self.selectedPartName is None:
            return NSMakeSize(xStep, yStep)

        try:
            glyph = font.glyphs[self.selectedPartName]
        except Exception:
            glyph = None
        if glyph is None:
            return NSMakeSize(xStep, yStep)

        layer = self.previewLayerForGlyph(glyph, font)
        bounds = None
        if layer is not None:
            try:
                closedPath = self.layerPath(layer, ["completeBezierPath", "drawBezierPath", "bezierPath"])
                openPath = self.layerPath(layer, ["completeOpenBezierPath", "drawOpenBezierPath", "openBezierPath"])
                bounds = self.unionBoundsForPaths([closedPath, openPath])
            except Exception:
                bounds = None

            try:
                width = float(getattr(layer, "width"))
                if width > 0:
                    xStep = width
            except Exception:
                pass

        if bounds is not None:
            try:
                width = float(bounds.size.width)
                if width > 0:
                    xStep = width
            except Exception:
                pass
            try:
                height = float(bounds.size.height)
                if height > 0:
                    yStep = height
            except Exception:
                pass

        # Thin/open parts can have an almost-zero bbox in one direction. In that
        # case, use the other dimension so a brush stroke does not create a huge
        # number of overlapping components.
        if xStep <= 0:
            xStep = fallback
        if yStep <= 0:
            yStep = xStep if xStep > 0 else fallback
        return NSMakeSize(float(xStep), float(yStep))

    @objc.python_method
    def startPaintingStroke(self, theEvent):
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
        self.resetPaintingState()
        self.paintingMode = True
        self.paintingLayer = layer
        self.paintingGlyph = glyph
        self.paintingPartName = self.selectedPartName
        self.paintingStepSize = self.selectedPartStepSize()
        self.paintAnchorPoint = self.lastLocation
        self.lastPaintCell = None
        self.paintedCells = set()
        self.paintingUndoOpen = False
        self.paintingDidDrag = False
        self.paintStrokeComponents = []

        try:
            glyph.beginUndo()
            self.paintingUndoOpen = True
            layer.clearSelection()
            self.paintAtEvent(theEvent)
        except Exception:
            self.finishPainting()
            raise

    @objc.python_method
    def markPaintingAsDrag(self):
        """Switch the current stroke from single-click insertion to drag-painting."""
        if getattr(self, "paintingDidDrag", False):
            return
        self.paintingDidDrag = True

        # A single click should leave the inserted component selected. Once the
        # user drags, the stroke becomes brush painting, so remove selection
        # from the first stamp and from any stamps created during the stroke.
        try:
            if self.paintingLayer is not None:
                self.paintingLayer.clearSelection()
        except Exception:
            pass
        for component in getattr(self, "paintStrokeComponents", []):
            try:
                component.selected = False
            except Exception:
                pass

    @objc.python_method
    def paintAtEvent(self, theEvent):
        if not getattr(self, "paintingMode", False):
            return
        try:
            graphicView = self.editViewController().graphicView()
            rawLocation = graphicView.getActiveLocation_(theEvent)
        except Exception:
            return
        self.rawLocation = rawLocation
        try:
            self.lastLayer = graphicView.activeLayer()
        except Exception:
            self.lastLayer = self.paintingLayer

        currentCell = self.paintCellForRawLocation(rawLocation)
        if currentCell is None:
            return

        previousCell = self.lastPaintCell
        if previousCell is None:
            cells = [currentCell]
        else:
            cells = self.paintCellsBetween(previousCell, currentCell)

        for cell in cells:
            self.paintCell(cell)
        self.lastPaintCell = currentCell
        try:
            Glyphs.redraw()
        except Exception:
            pass

    @objc.python_method
    def paintCellForRawLocation(self, rawLocation):
        if rawLocation is None:
            return None
        anchor = self.paintAnchorPoint
        step = self.paintingStepSize
        if anchor is None:
            anchor = self.snapPointToGrid(rawLocation)
            self.paintAnchorPoint = anchor
        if step is None:
            step = self.selectedPartStepSize()
            self.paintingStepSize = step
        try:
            xStep = float(step.width)
            yStep = float(step.height)
            if xStep <= 0 or yStep <= 0:
                return None
            x = int(round((float(rawLocation.x) - float(anchor.x)) / xStep))
            y = int(round((float(rawLocation.y) - float(anchor.y)) / yStep))
            return (x, y)
        except Exception:
            return None

    @objc.python_method
    def paintCellsBetween(self, startCell, endCell):
        try:
            dx = int(endCell[0] - startCell[0])
            dy = int(endCell[1] - startCell[1])
            steps = max(abs(dx), abs(dy))
            if steps <= 0:
                return [endCell]
            cells = []
            for index in range(1, steps + 1):
                x = int(round(startCell[0] + dx * index / float(steps)))
                y = int(round(startCell[1] + dy * index / float(steps)))
                cell = (x, y)
                if not cells or cells[-1] != cell:
                    cells.append(cell)
            return cells
        except Exception:
            return [endCell]

    @objc.python_method
    def pointForPaintCell(self, cell):
        anchor = self.paintAnchorPoint
        step = self.paintingStepSize
        if anchor is None or step is None:
            return None
        try:
            return NSMakePoint(
                float(anchor.x) + int(cell[0]) * float(step.width),
                float(anchor.y) + int(cell[1]) * float(step.height),
            )
        except Exception:
            return None

    @objc.python_method
    def paintCell(self, cell):
        if cell in self.paintedCells:
            return
        point = self.pointForPaintCell(cell)
        if point is None:
            return
        layer = self.paintingLayer
        partName = self.paintingPartName
        if layer is None or partName is None:
            return
        component = GSComponent(partName)
        component.position = point
        layer.shapes.append(component)
        try:
            component.selected = not getattr(self, "paintingDidDrag", False)
        except Exception:
            pass
        try:
            self.paintStrokeComponents.append(component)
        except Exception:
            pass
        self.paintedCells.add(cell)
        self.lastLocation = point

    @objc.python_method
    def updateModifierState(self, theEvent):
        self.optionKeyDown = self.isOptionKeyPressed(theEvent)
        self.commandKeyDown = self.isCommandKeyPressed(theEvent)
        return self.isSelectModifierDown()

    @objc.python_method
    def forcePartBrushCursor(self, theEvent=None):
        # Some Glyphs/PyObjC combinations do not refresh the cursor rects
        # immediately for Python tools. Setting the cursor during activation
        # and mouse events makes the active Part Brush state visible.
        try:
            if theEvent is not None:
                self.updateModifierState(theEvent)
            cursor = self.standardCursor()
            if cursor is not None:
                cursor.set()
        except Exception:
            pass

    @objc.python_method
    def forwardEventToSelectTool(self, selectorName, theEvent):
        """Temporarily hand mouse handling back to Glyphs' built-in SelectTool behavior."""
        try:
            selector = getattr(super(PartBrush, self), selectorName)
            return selector(theEvent)
        except AttributeError:
            pass
        except Exception:
            print(traceback.format_exc())
            Glyphs.showMacroWindow()
            return None

        try:
            selector = getattr(SelectTool, selectorName)
        except Exception:
            selector = None
        if selector is not None:
            try:
                return selector(self, theEvent)
            except Exception:
                print(traceback.format_exc())
                Glyphs.showMacroWindow()
                return None
        return None

    def flagsChanged_(self, theEvent):
        try:
            wasSelectModifierDown = self.isSelectModifierDown()
            isSelectModifierDown = self.updateModifierState(theEvent)

            # Cursor/redraw only when the mode actually changes. During normal
            # Option/Command dragging, the event stream must go straight to
            # SelectTool without our preview/palette work in between.
            if wasSelectModifierDown != isSelectModifierDown:
                if isSelectModifierDown:
                    self.clearPreviewLocation()
                self.forcePartBrushCursor()
                Glyphs.redraw()
        except Exception:
            print(traceback.format_exc())
            Glyphs.showMacroWindow()

    def mouseMoved_(self, theEvent):
        wasSelectModifierDown = self.isSelectModifierDown()
        selectModifierDown = self.updateModifierState(theEvent)
        if selectModifierDown:
            if not wasSelectModifierDown:
                self.clearPreviewLocation()
                self.forcePartBrushCursor()
                Glyphs.redraw()
            return self.forwardEventToSelectTool("mouseMoved_", theEvent)
        self.updateMouseLocation(theEvent)

    def mouseDragged_(self, theEvent):
        # Critical performance path: when Option/Alt or Command started a
        # temporary SelectTool gesture, do absolutely no Part Brush UI work here.
        # No cursor reset, no preview clearing, no Glyphs.redraw(), no palette
        # refresh check — just hand the event to the native SelectTool.
        if self.temporarySelectMode:
            return self.forwardEventToSelectTool("mouseDragged_", theEvent)

        selectModifierDown = self.updateModifierState(theEvent)
        if getattr(self, "paintingMode", False):
            self.markPaintingAsDrag()
            return self.paintAtEvent(theEvent)
        if selectModifierDown:
            self.clearPreviewLocation()
            return self.forwardEventToSelectTool("mouseDragged_", theEvent)
        self.updateMouseLocation(theEvent)

    def mouseUp_(self, theEvent):
        try:
            if self.temporarySelectMode:
                try:
                    return self.forwardEventToSelectTool("mouseUp_", theEvent)
                finally:
                    self.temporarySelectMode = False
                    selectModifierDown = self.updateModifierState(theEvent)
                    if selectModifierDown:
                        self.clearPreviewLocation()
                    else:
                        self.updateMouseLocation(theEvent)
                    self.forcePartBrushCursor()
                    Glyphs.redraw()

            selectModifierDown = self.updateModifierState(theEvent)
            if getattr(self, "paintingMode", False):
                return self.finishPainting(theEvent)
            if selectModifierDown:
                self.clearPreviewLocation()
                Glyphs.redraw()
        except Exception:
            print(traceback.format_exc())
            Glyphs.showMacroWindow()

    def mouseDown_(self, theEvent):
        try:
            selectModifierDown = self.updateModifierState(theEvent)

            if selectModifierDown:
                self.finishPainting()
                self.temporarySelectMode = True
                self.clearPreviewLocation()
                self.forcePartBrushCursor()
                return self.forwardEventToSelectTool("mouseDown_", theEvent)

            self.temporarySelectMode = False
            self.forcePartBrushCursor()
            self.startPaintingStroke(theEvent)
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
            if self.isSelectModifierDown() or getattr(self, "temporarySelectMode", False) or getattr(self, "paintingMode", False):
                return
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
        # Hide the floating palette when the user switches away from Glyphs.
        self.window.setHidesOnDeactivate_(True)
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

        self.refreshButton = NSButton.alloc().initWithFrame_(NSMakeRect(12, 5, 76, 24))
        self.refreshButton.setTitle_("Refresh")
        self.refreshButton.setTarget_(self)
        self.refreshButton.setAction_("refresh:")
        self.statusBar.addSubview_(self.refreshButton)

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
        self.storeSharedPalette()

    def windowWillClose_(self, notification):
        try:
            self.clearSharedPalette()
        except Exception:
            pass

    def windowDidResize_(self, notification):
        try:
            if self.window is not None and self.window.isVisible():
                self.layoutViews()
                self.updateGrid(force=True)
        except Exception:
            print(traceback.format_exc())
            Glyphs.showMacroWindow()

    def refresh_(self, sender):
        self.adoptSharedPalette()
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
        owner = PartBrush._activePaletteOwner
        if owner is not None and owner is not self:
            try:
                return owner.updateGridIfNeeded(sender)
            except Exception:
                pass

        # Never let passive UI callbacks compete with the native SelectTool
        # gesture. This keeps Option-copy and Command-move feeling immediate.
        if (
            getattr(self, "temporarySelectMode", False)
            or getattr(self, "paintingMode", False)
            or self.isSelectModifierDown()
        ):
            return

        self.adoptSharedPalette()
        if self.window is not None and self.window.isVisible():
            # UPDATEINTERFACE can fire very often. Manual Refresh and activation
            # still update immediately, but passive checks are throttled.
            try:
                now = time.time()
                if now - getattr(self, "lastPassiveGridCheck", 0.0) < 0.50:
                    return
                self.lastPassiveGridCheck = now
            except Exception:
                pass
            self.updateGrid(force=False)

    @objc.python_method
    def updateGrid(self, force=False):
        self.adoptSharedPalette()
        font = Glyphs.font
        if font is None:
            self.partNames = []
            self.selectedPartName = None
            self.previewReferenceSize = None
            self.lastGridSignature = None
            self.setStatus("No font open")
            self.populateGrid([])
            return

        masterId = font.selectedFontMaster.id if font.selectedFontMaster else ""
        try:
            fontIdentity = id(font)
        except Exception:
            fontIdentity = str(font)
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
        # Selection changes should not rebuild the palette: rebuilding replaces
        # the scroll view document view and can make the palette jump to the top
        # after the user inserts a part. Only data/layout changes belong here.
        signature = (fontIdentity, font.familyName, masterId, width, height, tuple(partNames))
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

        clipView = self.scrollView.contentView()
        clipBounds = clipView.bounds()
        previousOrigin = clipBounds.origin
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
        self.previewReferenceSize = self.previewReferenceSizeForPartNames(partNames)

        for index, name in enumerate(partNames):
            col = index % columns
            row = index // columns
            x = self.padding + col * (cellSize + self.cellGap)
            y = self.padding + row * (cellSize + self.cellGap)

            button = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, cellSize, cellSize))
            button.setButtonType_(NSButtonTypeMomentaryChange)
            button.setBezelStyle_(NSBezelStyleRegularSquare)
            button.setImagePosition_(NSImageOnly)
            selected = name == self.selectedPartName
            button.setImage_(self.imageForPart(name, max(18, cellSize - 14), selected))
            button.setTarget_(self)
            button.setAction_("selectPart:")
            button.setTag_(index)
            button.setToolTip_(name)
            if selected:
                button.setState_(NSOnState)
            else:
                button.setState_(NSOffState)
            self.buttons.append(button)
            self.gridView.addSubview_(button)

        self.scrollView.setDocumentView_(self.gridView)
        self.storeSharedPalette()
        try:
            maxY = max(0, contentHeight - visibleHeight)
            restoreY = min(max(0, previousOrigin.y), maxY)
            clipView.scrollToPoint_(NSMakePoint(0, restoreY))
            self.scrollView.reflectScrolledClipView_(clipView)
        except Exception:
            pass
        self.updateButtonStates()

    @objc.python_method
    def updateButtonStates(self):
        for index, button in enumerate(self.buttons):
            if index >= len(self.partNames):
                continue
            name = self.partNames[index]
            selected = name == self.selectedPartName
            if selected:
                button.setState_(NSOnState)
            else:
                button.setState_(NSOffState)
            try:
                frame = button.frame()
                imageSize = int(max(18, min(frame.size.width, frame.size.height) - 14))
                button.setImage_(self.imageForPart(name, imageSize, selected))
                button.setToolTip_(("Selected: " if selected else "") + name)
                button.setNeedsDisplay_(True)
            except Exception:
                pass

    @objc.python_method
    def selectionColor(self, alpha=1.0):
        if not getattr(self, "toolActive", False):
            color = NSColor.colorWithCalibratedWhite_alpha_(0.54, 1.0)
            try:
                return color.colorWithAlphaComponent_(alpha)
            except Exception:
                return color

        try:
            color = NSColor.controlAccentColor()
        except Exception:
            try:
                color = NSColor.selectedControlColor()
            except Exception:
                color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.45, 1.0, 1.0)
        try:
            return color.colorWithAlphaComponent_(alpha)
        except Exception:
            return color

    @objc.python_method
    def previewReferenceSizeForPartNames(self, partNames):
        """Return the largest preview width/height used as the palette scale reference."""
        font = Glyphs.font
        if font is None:
            return None

        maxWidth = 0.0
        maxHeight = 0.0
        for name in partNames:
            try:
                glyph = font.glyphs[name]
                if glyph is None:
                    continue
                layer = self.previewLayerForGlyph(glyph, font)
                if layer is None:
                    continue
                closedPath = self.layerPath(layer, ["completeBezierPath", "drawBezierPath", "bezierPath"])
                openPath = self.layerPath(layer, ["completeOpenBezierPath", "drawOpenBezierPath", "openBezierPath"])
                bounds = self.unionBoundsForPaths([closedPath, openPath])
                if bounds is None:
                    continue
                if bounds.size.width > maxWidth:
                    maxWidth = bounds.size.width
                if bounds.size.height > maxHeight:
                    maxHeight = bounds.size.height
            except Exception:
                pass

        if maxWidth <= 0 or maxHeight <= 0:
            return None
        return NSMakeSize(maxWidth, maxHeight)

    @objc.python_method
    def imageForPart(self, glyphName, size, selected=False):
        image = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
        image.lockFocus()
        try:
            NSColor.clearColor().setFill()
            NSRectFill(NSMakeRect(0, 0, size, size))

            if selected:
                highlightRect = NSMakeRect(1.5, 1.5, max(0, size - 3.0), max(0, size - 3.0))
                highlightPath = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(highlightRect, 7.0, 7.0)
                self.selectionColor(0.16).setFill()
                highlightPath.fill()

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

            # Use one shared scale for the whole palette instead of fitting every
            # part independently. This keeps similarly shaped parts visually
            # comparable: the largest part fills the thumbnail area, and smaller
            # parts stay smaller relative to it.
            referenceSize = self.previewReferenceSize
            if referenceSize is not None and referenceSize.width > 0 and referenceSize.height > 0:
                scale = min(available / referenceSize.width, available / referenceSize.height)
            else:
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
            if selected:
                try:
                    borderRect = NSMakeRect(1.5, 1.5, max(0, size - 3.0), max(0, size - 3.0))
                    borderPath = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(borderRect, 7.0, 7.0)
                    borderPath.setLineWidth_(2.5)
                    self.selectionColor(0.95).setStroke()
                    borderPath.stroke()
                except Exception:
                    pass
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
            if PartBrush._callbackOwner is self:
                Glyphs.removeCallback(self.updateGridIfNeeded)
                PartBrush._callbacksRegistered = False
                PartBrush._callbackOwner = None
        except Exception:
            pass
