# encoding: utf-8
from __future__ import division, print_function, unicode_literals

import objc
import traceback

from GlyphsApp import (
    Glyphs,
    GSComponent,
    WINDOW_MENU,
    DOCUMENTACTIVATED,
    UPDATEINTERFACE,
)
from GlyphsApp.plugins import GeneralPlugin

from AppKit import (
    NSAffineTransform,
    NSApp,
    NSBackingStoreBuffered,
    NSBezelStyleRegularSquare,
    NSButton,
    NSButtonTypeMomentaryChange,
    NSColor,
    NSFont,
    NSImage,
    NSImageOnly,
    NSLineBreakByTruncatingTail,
    NSMenuItem,
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
from Foundation import NSMakePoint, NSMakeRect, NSMakeSize

try:
    from GlyphsApp.UI import MenuItem
except Exception:
    MenuItem = None

try:
    from AppKit import NSEdgeInsetsMake
except Exception:
    NSEdgeInsetsMake = None

try:
    from AppKit import NSFloatingWindowLevel
except Exception:
    NSFloatingWindowLevel = 3


class PartInserterFlippedGridView(NSView):
    def isFlipped(self):
        return True


class PartInserterPreviewButton(NSButton):
    """Palette button that should not steal focus from the Glyphs edit view."""

    def acceptsFirstResponder(self):
        return False

    def needsPanelToBecomeKey(self):
        return False


class PartInserter(GeneralPlugin):
    """Floating window for inserting glyph components named _part.*."""

    windowAutosaveName = "com.sur88.PartInserter.window"
    partPrefix = "_part."
    cellSize = 72
    cellGap = 8
    padding = 14
    statusBarHeight = 34

    @objc.python_method
    def settings(self):
        self.name = Glyphs.localize({"en": "Part Inserter"})
        self.window = None
        self.scrollView = None
        self.gridView = None
        self.statusBar = None
        self.statusText = None
        self.partNames = []
        self.previewReferenceSize = None
        self.lastGridSignature = None

    @objc.python_method
    def start(self):
        try:
            if MenuItem is not None:
                menuItem = MenuItem(self.name, action=self.showWindow_, target=self)
            else:
                menuItem = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    self.name, self.showWindow_, ""
                )
                menuItem.setTarget_(self)
            Glyphs.menu[WINDOW_MENU].append(menuItem)

            Glyphs.addCallback(self.updateGridIfNeeded, DOCUMENTACTIVATED)
            Glyphs.addCallback(self.updateGridIfNeeded, UPDATEINTERFACE)
        except Exception:
            print(traceback.format_exc())
            Glyphs.showMacroWindow()

    def showWindow_(self, sender):
        try:
            if self.window is None:
                self.buildWindow()
            self.updateGrid(force=True)
            # Show the palette without making it the active/key window, so the
            # next canvas action still goes straight to the Glyphs edit view.
            try:
                self.window.orderFrontRegardless()
            except Exception:
                self.window.orderFront_(self)
            self.refocusEditView()
        except Exception:
            print(traceback.format_exc())
            Glyphs.showMacroWindow()

    def refresh_(self, sender):
        self.updateGrid(force=True)
        self.refocusEditView()

    def insertPart_(self, sender):
        try:
            index = sender.tag()
            if index < 0 or index >= len(self.partNames):
                return

            partName = self.partNames[index]
            font = Glyphs.font
            if font is None or not font.selectedLayers:
                Glyphs.showNotification(
                    "Part Inserter", "Open a font and select a glyph layer first."
                )
                return

            layer = font.selectedLayers[0]
            glyph = layer.parent
            if glyph is None:
                return

            glyph.beginUndo()
            try:
                component = GSComponent(partName)
                layer.clearSelection()
                layer.shapes.append(component)
                component.selected = True
                Glyphs.redraw()
            finally:
                glyph.endUndo()

            self.refocusEditView()
        except Exception:
            print(traceback.format_exc())
            Glyphs.showMacroWindow()

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
        self.window.setTitle_("Parts")
        self.window.setFloatingPanel_(True)
        # Hide the floating palette when the user switches away from Glyphs.
        self.window.setHidesOnDeactivate_(True)
        try:
            self.window.setLevel_(NSFloatingWindowLevel)
        except Exception:
            pass
        try:
            # Let controls work without making the palette the key window.
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

        # Main area: grows/shrinks with the window, but never covers the status bar.
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

        # Bottom status bar: fixed height, one line, pinned to the bottom edge.
        self.statusBar = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, self.statusBarHeight))
        self.statusBar.setAutoresizingMask_(NSViewWidthSizable | NSViewMaxYMargin)
        self.statusBar.setAutoresizesSubviews_(True)
        content.addSubview_(self.statusBar)

        refreshButton = PartInserterPreviewButton.alloc().initWithFrame_(NSMakeRect(12, 5, 76, 24))
        refreshButton.setTitle_("Refresh")
        refreshButton.setTarget_(self)
        refreshButton.setAction_("refresh:")
        self.statusBar.addSubview_(refreshButton)

        self.statusText = NSTextField.alloc().initWithFrame_(
            NSMakeRect(98, 8, initialWidth - 110, 18)
        )
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

    def windowDidResize_(self, notification):
        try:
            if self.window is not None and self.window.isVisible():
                self.layoutViews()
                self.updateGrid(force=True)
        except Exception:
            print(traceback.format_exc())
            Glyphs.showMacroWindow()

    @objc.python_method
    def updateGridIfNeeded(self, sender=None):
        if self.window is not None and self.window.isVisible():
            self.updateGrid(force=False)

    @objc.python_method
    def updateGrid(self, force=False):
        font = Glyphs.font
        if font is None:
            self.partNames = []
            self.previewReferenceSize = None
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
                width = 0
                height = 0
        partNames = sorted([g.name for g in font.glyphs if g.name.startswith(self.partPrefix)])
        signature = (font.familyName, masterId, width, height, tuple(partNames))

        if not force and signature == self.lastGridSignature:
            return

        self.lastGridSignature = signature
        self.partNames = partNames
        self.setStatus("%d parts in %s" % (len(partNames), font.familyName))
        self.populateGrid(partNames)

    @objc.python_method
    def setStatus(self, text):
        if self.statusText is not None:
            self.statusText.setStringValue_(text)

    @objc.python_method
    def populateGrid(self, partNames):
        if self.scrollView is None:
            return

        clipBounds = self.scrollView.contentView().bounds()
        contentWidth = int(clipBounds.size.width)
        if contentWidth <= 0:
            contentWidth = 420

        visibleHeight = int(clipBounds.size.height)
        leftPadding = self.padding
        rightPadding = self.padding
        usableWidth = max(1, contentWidth - leftPadding - rightPadding)

        columns = max(1, int((usableWidth + self.cellGap) / (self.cellSize + self.cellGap)))
        # Keep a real right inset. Button bezels can draw a hair outside their frame,
        # so leave one extra point instead of filling the clip view exactly.
        while columns > 1:
            candidateSize = int((usableWidth - self.cellGap * (columns - 1) - 1) / columns)
            if candidateSize >= 36:
                break
            columns -= 1

        cellSize = int((usableWidth - self.cellGap * (columns - 1) - 1) / columns)
        cellSize = max(36, cellSize)

        rows = max(1, int((len(partNames) + columns - 1) / columns))
        contentHeight = leftPadding * 2 + rows * cellSize + max(0, rows - 1) * self.cellGap
        contentHeight = max(contentHeight, visibleHeight)

        self.gridView = PartInserterFlippedGridView.alloc().initWithFrame_(NSMakeRect(0, 0, contentWidth, contentHeight))
        self.previewReferenceSize = self.previewReferenceSizeForPartNames(partNames)

        for index, name in enumerate(partNames):
            col = index % columns
            row = index // columns
            x = leftPadding + col * (cellSize + self.cellGap)
            y = leftPadding + row * (cellSize + self.cellGap)

            button = PartInserterPreviewButton.alloc().initWithFrame_(NSMakeRect(x, y, cellSize, cellSize))
            button.setButtonType_(NSButtonTypeMomentaryChange)
            button.setBezelStyle_(NSBezelStyleRegularSquare)
            button.setImagePosition_(NSImageOnly)
            button.setImage_(self.imageForPart(name, max(18, cellSize - 14)))
            button.setTarget_(self)
            button.setAction_("insertPart:")
            button.setTag_(index)
            button.setToolTip_(name)
            self.gridView.addSubview_(button)

        self.scrollView.setDocumentView_(self.gridView)
        try:
            self.scrollView.contentView().scrollToPoint_(NSMakePoint(0, 0))
            self.scrollView.reflectScrolledClipView_(self.scrollView.contentView())
        except Exception:
            pass

    @objc.python_method
    def refocusEditView(self):
        """Return keyboard/mouse focus to the active Glyphs edit view after palette actions."""
        try:
            document = Glyphs.currentDocument
            if callable(document):
                document = document()
            if document is None:
                return

            windowController = document.windowController()
            if windowController is None:
                return

            window = windowController.window()
            graphicView = None

            # GeneralPlugin does not always have self.editViewController(), so ask
            # the document window controller for the active edit view controller.
            for selector in ("activeEditViewController", "editViewController"):
                try:
                    editViewControllerMethod = getattr(windowController, selector)
                    editViewController = editViewControllerMethod()
                    if editViewController is not None:
                        graphicView = editViewController.graphicView()
                        if graphicView is not None:
                            break
                except Exception:
                    pass

            try:
                NSApp.activateIgnoringOtherApps_(True)
            except Exception:
                pass

            if window is not None:
                try:
                    window.makeKeyWindow()
                except Exception:
                    pass
                try:
                    window.makeMainWindow()
                except Exception:
                    pass
                if graphicView is not None:
                    try:
                        window.makeFirstResponder_(graphicView)
                    except Exception:
                        pass
        except Exception:
            pass

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

            # Use one shared scale for the whole palette instead of fitting every
            # part independently. This keeps differently sized parts comparable:
            # the largest part fills the thumbnail area, and smaller parts remain
            # smaller relative to it.
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
