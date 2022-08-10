from collections import defaultdict, namedtuple
from dataclasses import dataclass
from enum import Enum
import enum
import json
import logging
from pathlib import Path
import time
from scipy import stats
from typing import (
    Any,
    DefaultDict,
    Dict,
    List,
    MutableMapping,
    NamedTuple,
    Optional,
    Set,
    Tuple,
    Union,
)
import pandas as pd
import numpy as np
import pyperclip
from PySide6.QtCore import (
    QObject,
    Slot,
    QSortFilterProxyModel,
    Signal,
    QSize,
    QThread,
    QSemaphore,
    Qt,
    QBasicTimer,
    QCoreApplication,
    QModelIndex,
    QPersistentModelIndex,
    QAbstractTableModel,
    QMutexLocker,
)
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QTableView,
    QApplication,
    QWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QMainWindow,
    QLineEdit,
    QTextEdit,
    QLabel,
    QHeaderView,
    QAbstractItemView,
    QPushButton,
    QMenuBar,
    QWidgetAction,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QSizePolicy,
    QGridLayout,
)
from pyqtgraph import (
    PlotWidget,
    DateAxisItem,
    AxisItem,
    PlotCurveItem,
    PlotDataItem,
    ViewBox,
    Point,
    functions,
    mkPen,
)
from QTableWidgetFloatItem import QTableWidgetFloatItem
from cache import PersistMapping
from classjobConfig import ClassJobConfig
from ff14marketcalc import get_profit, get_revenue, log_time, print_recipe
from gathererWorker.gathererWorker import GathererWindow
from itemCleaner.itemCleaner import ItemCleanerForm
from retainerWorker.models import ListingData
from universalis.models import Listings
from craftingWorker import CraftingWorker
from retainerWorker.retainerWorker import RetainerWorker
from universalis.universalis import (
    UniversalisManager,
    get_listings,
    set_seller_id,
)
from universalis.universalis import save_to_disk as universalis_save_to_disk
from xivapi.models import ClassJob, Item, Recipe, RecipeCollection
from xivapi.xivapi import (
    XivapiManager,
    get_classjob_doh_list,
    get_recipe_by_id,
    get_recipes,
    search_recipes,
)
from xivapi.xivapi import save_to_disk as xivapi_save_to_disk

logging.basicConfig(
    level=logging.INFO,
    format=" %(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(".data/debug.log", mode="w"),
        logging.StreamHandler(),
    ],
)

_logger = logging.getLogger(__name__)
_logger.setLevel(logging.DEBUG)

world_id = 55


def create_default_directories() -> None:
    Path(".data/").mkdir(exist_ok=True)
    # Path(".logs/").mkdir(exist_ok=True)


create_default_directories()


class MainWindow(QMainWindow):
    class RecipeTableView(QTableView):
        def __init__(self, parent: Optional[QWidget] = None) -> None:
            super().__init__(parent)
            # self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            self.verticalHeader().hide()
            self.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.setSortingEnabled(True)
            self.sortByColumn(7, Qt.DescendingOrder)

        def add_recipe(self, recipe: Recipe) -> None:
            self.model().add_recipe(recipe)
            self.resizeColumnsToContents()

        @Slot(int, float)
        def set_profit(self, recipe_id: int, profit: float) -> None:
            self.set_row_data(recipe_id, profit=profit)

        def set_row_data(
            self,
            recipe_id: int,
            profit: Optional[float] = None,
            velocity: Optional[float] = None,
            listing_count: Optional[int] = None,
        ) -> None:
            self.model().set_row_data(
                recipe_id,
                profit=profit,
                velocity=velocity,
                listing_count=listing_count,
            )
            self.resizeColumnsToContents()

        def classjob_level_changed(self) -> None:
            self.dataChanged(
                self.model().index(0, 1),
                self.model().index(self.model().rowCount(), 1),
                [Qt.BackgroundRole],
            )

    class RecipeTableProxyModel(QSortFilterProxyModel):
        def __init__(self, parent: Optional[QWidget] = None) -> None:
            super().__init__(parent)
            self.setDynamicSortFilter(True)
            self.setFilterCaseSensitivity(Qt.CaseInsensitive)
            self.setFilterKeyColumn(2)

        def lessThan(self, left, right):
            left_data = self.sourceModel().data(left, Qt.UserRole)
            right_data = self.sourceModel().data(right, Qt.UserRole)
            if left_data is not None and right_data is not None:
                return left_data < right_data
            else:
                return super().lessThan(left, right)
            # elif right_data is None:
            #     return False
            # else:
            #     return True

        def filterAcceptsRow(
            self,
            source_row: int,
            source_parent: Union[QModelIndex, QPersistentModelIndex],
        ) -> bool:
            # source_model = self.sourceModel()
            # if (
            #     len(self.gathering_item_id_filter_set) == 0
            #     or source_model.table_data[source_row][-1]
            #     in self.gathering_item_id_filter_set
            # ):
            #     return super().filterAcceptsRow(source_row, source_parent)
            # return False
            return super().filterAcceptsRow(source_row, source_parent)

        def add_recipe(self, recipe: Recipe) -> None:
            self.sourceModel().add_recipe(recipe)

        def set_row_data(
            self,
            recipe_id: int,
            profit: Optional[float] = None,
            velocity: Optional[float] = None,
            listing_count: Optional[int] = None,
        ) -> None:
            self.sourceModel().set_row_data(
                recipe_id,
                profit=profit,
                velocity=velocity,
                listing_count=listing_count,
            )

    class RecipeTableModel(QAbstractTableModel):
        @dataclass
        class RowData:
            classjob_abbreviation: str  # Job
            classjob_level: int  # Lvl
            item_name: str  # Item
            profit: Optional[float] = None  # Profit
            velocity: Optional[float] = None  # Velocity
            listing_count: Optional[int] = None  # Lists
            speed: Optional[float] = None  # Sp
            score: Optional[float] = None  # Score
            recipe_id: int = None
            classjob_id: Optional[int] = None

            def __getitem__(self, item: int) -> Any:
                if item == 0:
                    return self.classjob_abbreviation
                elif item == 1:
                    return self.classjob_level
                elif item == 2:
                    return self.item_name
                elif item == 3:
                    return self.profit
                elif item == 4:
                    return self.velocity
                elif item == 5:
                    return self.listing_count
                elif item == 6:
                    return self.speed
                elif item == 7:
                    return self.score
                elif item == 8:
                    return self.recipe_id
                else:
                    raise IndexError(f"Invalid index {item}")

        def __init__(
            self,
            parent: Optional[QObject],
            classjob_config: PersistMapping[int, ClassJobConfig],
        ) -> None:
            super().__init__(parent)
            self.classjob_config = classjob_config
            self.table_data: List[MainWindow.RecipeTableModel.RowData] = []
            self.recipe_id_to_row_index_dict: Dict[int, int] = {}
            self.header_data: List[str] = [
                "Job",
                "Lvl",
                "Item",
                "Profit",
                "Velocity",
                "Lists",
                "Sp",
                "Score",
            ]

        def rowCount(
            self, parent: Union[QModelIndex, QPersistentModelIndex] = None
        ) -> int:
            return len(self.table_data)

        def columnCount(
            self, parent: Union[QModelIndex, QPersistentModelIndex] = None
        ) -> int:
            return 8

        def data(  # type: ignore[override]
            self,
            index: QModelIndex,
            role: Qt.ItemDataRole = Qt.DisplayRole,
        ) -> Any:
            if not index.isValid():
                return None
            if role == Qt.DisplayRole:
                column = index.column()
                cell_data = self.table_data[index.row()][column]
                if cell_data is None:
                    return ""
                if column == 3 or column == 7:  # profit, score
                    return f"{cell_data:,.0f}"
                elif column == 4 or column == 6:  # velocity, speed
                    return f"{cell_data:,.2f}"
                elif (
                    column <= 2 or column == 5
                ):  # classjob_abbreviation, classjob_level, item_name, listing_count
                    return cell_data
                else:
                    return cell_data
            elif role == Qt.UserRole:
                return self.table_data[index.row()][index.column()]
            elif role == Qt.BackgroundRole:
                column = index.column()
                row = index.row()
                cell_data = self.table_data[row][column]
                if cell_data is None:
                    return ""
                if column == 1:  # classjob_level
                    classjob_id = self.table_data[row].classjob_id
                    return QBrush(
                        QColor.fromHsl(
                            max(
                                min(
                                    120
                                    - (
                                        self.classjob_config[classjob_id].level
                                        - cell_data
                                    )
                                    * 24,
                                    120.0,
                                ),
                                0.0,
                            ),
                            127,
                            157,
                        )
                    )
                elif column == 3:  # profit
                    return QBrush(
                        QColor.fromHsl(
                            max(min(cell_data / (150000 / 120), 120.0), 0.0), 127, 157
                        )
                    )
                elif column == 6:  # speed
                    return QBrush(
                        QColor.fromHsl(max(min(cell_data * 40, 120.0), 0.0), 127, 157)
                    )
            return None

        def headerData(  # type: ignore[override]
            self,
            section: int,
            orientation: Qt.Orientation,
            role: Qt.ItemDataRole = Qt.DisplayRole,
        ) -> Optional[str]:
            if orientation == Qt.Horizontal and role == Qt.DisplayRole:
                return self.header_data[section]
            return None

        def add_recipe(self, recipe: Recipe) -> None:
            recipe_id = recipe.ID
            # _logger.debug(f"recipe_table_model.add_recipe: {recipe_id}")
            if recipe_id not in self.recipe_id_to_row_index_dict:
                row_count = self.rowCount()
                self.beginInsertRows(QModelIndex(), row_count, row_count)
                row_data = self.RowData(
                    classjob_abbreviation=recipe.ClassJob.Abbreviation,
                    classjob_level=recipe.RecipeLevelTable.ClassJobLevel,
                    item_name=recipe.ItemResult.Name,
                    recipe_id=recipe_id,
                    classjob_id=recipe.ClassJob.ID,
                )
                self.table_data.append(row_data)
                self.recipe_id_to_row_index_dict[row_data.recipe_id] = row_count
                self.endInsertRows()

        def set_row_data(
            self,
            recipe_id: int,
            profit: Optional[float] = None,
            velocity: Optional[float] = None,
            listing_count: Optional[int] = None,
        ) -> None:
            row_index = self.recipe_id_to_row_index_dict[recipe_id]
            row_data = self.table_data[row_index]
            if profit is not None:
                row_data.profit = profit
            if velocity is not None:
                row_data.velocity = velocity
            if listing_count is not None:
                row_data.listing_count = listing_count
            if row_data.velocity is not None and row_data.profit is not None:
                row_data.score = row_data.profit * row_data.velocity
            if row_data.velocity is not None and row_data.listing_count is not None:
                row_data.speed = row_data.velocity / max(row_data.listing_count, 1)
            self.dataChanged.emit(self.index(row_index, 3), self.index(row_index, 7))

    class RetainerTable(QTableWidget):
        def __init__(self, parent: QWidget, seller_id: int):
            super().__init__(parent)
            self.setColumnCount(4)
            self.setHorizontalHeaderLabels(
                ["Retainer", "Item", "Listed Price", "Min Price"]
            )
            self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            self.verticalHeader().hide()
            self.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.seller_id = seller_id
            self.table_data: Dict[
                int, List[List[QTableWidgetItem]]
            ] = {}  # itemID -> row -> column
            self.good_color = QColor(0, 255, 0, 50)
            self.bad_color = QColor(255, 0, 0, 50)

        def clear_contents(self) -> None:
            self.clearContents()
            self.setRowCount(0)
            self.table_data.clear()

        def get_min_price(self, listings: Listings) -> float:
            listing_prices = [
                listing.pricePerUnit
                for listing in listings.listings
                if listing.sellerID != self.seller_id
            ]
            if len(listing_prices) > 0:
                return min(listing_prices)
            else:
                return np.inf

        @Slot(list)
        def on_listing_data_updated(self, listing_data: ListingData) -> None:
            row_list_index = 0
            row_list = self.table_data.setdefault(listing_data.item.ID, [])
            for listing in listing_data.listings.listings:
                if listing.sellerID == self.seller_id:
                    if row_list_index < len(row_list):
                        row_data = row_list[row_list_index]
                        row_data[2].setText(f"{listing.pricePerUnit:,.0f}")
                        row_data[3].setText(
                            f"{self.get_min_price(listing_data.listings):,.0f}"
                        )
                    else:
                        row_data = [
                            QTableWidgetItem(listing.retainerName),
                            QTableWidgetItem(listing_data.item.Name),
                            QTableWidgetItem(f"{listing.pricePerUnit:,.0f}"),
                            QTableWidgetItem(
                                f"{self.get_min_price(listing_data.listings):,.0f}"
                            ),
                        ]
                        row_count = self.rowCount()
                        self.insertRow(row_count)
                        for column_index, widget in enumerate(row_data):
                            self.setItem(row_count, column_index, widget)
                        row_list.append(row_data)
                    if listing.pricePerUnit <= listing_data.listings.minPrice:
                        color = self.good_color
                    else:
                        color = self.bad_color
                    for table_widget_item in row_data:
                        table_widget_item.setBackground(color)
                    row_list_index += 1

    class PriceGraph(PlotWidget):
        class FmtAxesItem(AxisItem):
            def __init__(
                self,
                orientation,
                pen=None,
                textPen=None,
                linkView=None,
                parent=None,
                maxTickLength=-5,
                showValues=True,
                text="",
                units="",
                unitPrefix="",
                **args,
            ):
                super().__init__(
                    orientation,
                    pen,
                    textPen,
                    linkView,
                    parent,
                    maxTickLength,
                    showValues,
                    text,
                    units,
                    unitPrefix,
                    **args,
                )

            def tickStrings(self, values, scale, spacing):
                return [f"{v:,.0f}" for v in values]

        def __init__(self, parent=None, background="default", plotItem=None, **kargs):
            kargs["axisItems"] = {
                "bottom": DateAxisItem(),
                "left": MainWindow.PriceGraph.FmtAxesItem(orientation="left"),
                "right": MainWindow.PriceGraph.FmtAxesItem(orientation="right"),
            }
            super().__init__(parent, background, plotItem, **kargs)

            self.p1 = self.plotItem
            self.p1.getAxis("left").setLabel("Velocity", color="#00ffff")
            self.p1_pen = mkPen(color="#00ff00", width=2)

            ## create a new ViewBox, link the right axis to its coordinate system
            self.p2 = ViewBox()
            self.p1.showAxis("right")
            self.p1.scene().addItem(self.p2)
            self.p1.getAxis("right").linkToView(self.p2)
            self.p2.setXLink(self.p1)
            self.p1.getAxis("right").setLabel("Purchases", color="#00ff00")
            # # self.p1.vb.setLogMode("y", True)
            # self.p2.setLogMode(self.p1.getAxis("right"), True)
            # self.p1.getAxis("right").setLogMode(False, True)
            # self.p1.getAxis("right").enableAutoSIPrefix(False)

            ## create third ViewBox.
            ## this time we need to create a new axis as well.
            self.p3 = ViewBox()
            self.ax3 = MainWindow.PriceGraph.FmtAxesItem(orientation="right")
            self.p1.layout.addItem(self.ax3, 2, 3)
            self.p1.scene().addItem(self.p3)
            self.ax3.linkToView(self.p3)
            self.p3.setXLink(self.p1)
            self.p3.setYLink(self.p2)
            self.ax3.setZValue(-10000)
            self.ax3.setLabel("Listings", color="#ff00ff")
            self.ax3.hide()
            self.ax3.setGrid(128)
            # self.ax3.setLogMode(False, True)
            # self.p3.setLogMode("y", True)
            # self.ax3.hideAxis()
            # self.ax3.setLogMode(False, True)
            # self.ax3.enableAutoSIPrefix(False)

            self.updateViews()
            self.p1.vb.sigResized.connect(self.updateViews)

        @Slot()
        def updateViews(self) -> None:
            self.p2.setGeometry(self.p1.vb.sceneBoundingRect())
            self.p3.setGeometry(self.p1.vb.sceneBoundingRect())
            self.p2.linkedViewChanged(self.p1.vb, self.p2.XAxis)
            self.p3.linkedViewChanged(self.p1.vb, self.p3.XAxis)

        def auto_range(self):
            self.p2.enableAutoRange(axis="y")
            self.p3.enableAutoRange(axis="y")
            self.p1.vb.updateAutoRange()
            self.p2.updateAutoRange()
            self.p3.updateAutoRange()

            bounds = [np.inf, -np.inf]
            for items in (
                self.p1.vb.addedItems,
                self.p2.addedItems,
                self.p3.addedItems,
            ):
                for item in items:
                    _bounds = item.dataBounds(0)
                    if _bounds[0] is None or _bounds[1] is None:
                        continue
                    bounds[0] = min(_bounds[0], bounds[0])
                    bounds[1] = max(_bounds[1], bounds[1])
            if bounds[0] != np.inf and bounds[1] != -np.inf:
                self.p1.vb.setRange(xRange=bounds)

            bounds = [np.inf, -np.inf]
            for items in (
                self.p2.addedItems,
                self.p3.addedItems,
            ):
                for item in items:
                    _bounds = item.dataBounds(1)
                    if _bounds[0] is None or _bounds[1] is None:
                        continue
                    bounds[0] = min(_bounds[0], bounds[0])
                    bounds[1] = max(_bounds[1], bounds[1])
            if bounds[0] != np.inf and bounds[1] != -np.inf:
                self.p2.setRange(yRange=bounds)

        def wheelEvent(self, ev, axis=None):
            super().wheelEvent(ev)
            for vb in (
                self.p1.vb,
                self.p2,
                self.p3,
            ):
                if axis in (0, 1):
                    mask = [False, False]
                    mask[axis] = vb.state["mouseEnabled"][axis]
                else:
                    mask = vb.state["mouseEnabled"][:]
                s = 1.02 ** (
                    (ev.angleDelta().y() - ev.angleDelta().x())
                    * vb.state["wheelScaleFactor"]
                )  # actual scaling factor
                s = [(None if m is False else s) for m in mask]
                center = Point(
                    functions.invertQTransform(vb.childGroup.transform()).map(
                        ev.position()
                    )
                )

                vb._resetTarget()
                vb.scaleBy(s, center)
                ev.accept()
                vb.sigRangeChangedManually.emit(mask)

    # class JobLevelWidget(QWidget):
    #     def __init__(self, parent: Optional[QWidget] = ..., f: Qt.WindowFlags = ...) -> None:
    #         super().__init__(parent, f)

    class ClassJobLevelLayout(QHBoxLayout):
        joblevel_value_changed = Signal(int, int)

        def __init__(self, parent: QWidget, classjob_config: ClassJobConfig) -> None:
            self.classjob = ClassJob(**classjob_config.dict())
            super().__init__()
            self.label = QLabel(parent)
            self.label.setText(classjob_config.Abbreviation)
            self.label.setAlignment(Qt.AlignRight)  # type: ignore
            self.label.setAlignment(Qt.AlignCenter)  # type: ignore
            self.addWidget(self.label)
            self.spinbox = QSpinBox(parent)
            self.spinbox.setMaximum(90)
            self.spinbox.setValue(classjob_config.level)
            self.addWidget(self.spinbox)

            self.spinbox.valueChanged.connect(self.on_spinbox_value_changed)  # type: ignore

        def on_spinbox_value_changed(self, value: int) -> None:
            _logger.info(f"{self.classjob.Abbreviation} level changed to {value}")
            self.joblevel_value_changed.emit(self.classjob.ID, value)

    class RecipeDetails(QWidget):
        def __init__(
            self, crafting_worker: CraftingWorker, parent: Optional[QWidget] = None
        ) -> None:
            self.crafting_worker = crafting_worker
            super().__init__(parent)
            self.main_layout = QVBoxLayout()
            self.setLayout(self.main_layout)

            self.top_layout = QGridLayout()
            self.main_layout.addLayout(self.top_layout)
            self.top_layout.setColumnStretch(2, 1)

            self.profit_label = QLabel()
            self.top_layout.addWidget(self.profit_label, 0, 0, Qt.AlignRight)
            self.profit_label.setText("Profit:")

            self.profit_value_label = QLabel()
            self.top_layout.addWidget(self.profit_value_label, 0, 1)

            self.revenue_label = QLabel()
            self.top_layout.addWidget(self.revenue_label, 1, 0, Qt.AlignRight)
            self.revenue_label.setText("Revenue:")

            self.revenue_value_label = QLabel()
            self.top_layout.addWidget(self.revenue_value_label, 1, 1)

            self.cost_label = QLabel()
            self.top_layout.addWidget(self.cost_label, 2, 0, Qt.AlignRight)
            self.cost_label.setText("Cost:")

            self.cost_value_label = QLabel()
            self.top_layout.addWidget(self.cost_value_label, 2, 1)

            self.ingredients_table = QTreeWidget()
            self.ingredients_table.setColumnCount(6)
            self.ingredients_table.setHeaderLabels(
                [
                    "Ingredient",
                    "Action",
                    "Quantity",
                    "Profit",
                    "Crafting Cost",
                    "Market Cost",
                ]
            )
            self.ingredients_table.setSelectionBehavior(QTreeWidget.SelectRows)
            self.ingredients_table.header().setSectionResizeMode(
                QHeaderView.ResizeToContents
            )
            self.main_layout.addWidget(self.ingredients_table)

        def _add_recipe_to_table(
            self, recipe: Recipe, parent_widget_item: QTreeWidgetItem
        ):
            for ingredient_index in range(9):
                ingredient: Item = getattr(recipe, f"ItemIngredient{ingredient_index}")
                if ingredient:
                    ingredient_id = ingredient.ID
                    ingredient_quantity: int = getattr(
                        recipe, f"AmountIngredient{ingredient_index}"
                    )
                    ingredient_action = self.crafting_worker.get_aquire_action(
                        ingredient_id
                    )
                    ingredient_crafting_cost = self.crafting_worker.get_crafting_cost(
                        ingredient_id
                    )
                    ingredient_market_cost = self.crafting_worker.get_market_cost(
                        ingredient_id
                    )
                    ingredient_row_item = QTreeWidgetItem(
                        parent_widget_item,
                        [
                            ingredient.Name,
                            ingredient_action.name,
                            f"{ingredient_quantity}",
                            f"{abs(ingredient_market_cost - ingredient_crafting_cost):,.0f}"
                            if abs(ingredient_market_cost - ingredient_crafting_cost)
                            != np.inf
                            else "",
                            f"{ingredient_crafting_cost:,.0f}"
                            if ingredient_crafting_cost != np.inf
                            else "",
                            f"{ingredient_market_cost:,.0f}",
                        ],
                    )
                    parent_widget_item.addChild(ingredient_row_item)
                    if ingredient_action == CraftingWorker.AquireAction.CRAFT:
                        ingredient_recipe_list: Optional[Tuple[Recipe]] = getattr(
                            recipe, f"ItemIngredientRecipe{ingredient_index}"
                        )
                        assert ingredient_recipe_list is not None
                        # Assume all recipes are created equal
                        self._add_recipe_to_table(
                            ingredient_recipe_list[0], ingredient_row_item
                        )
                        ingredient_row_item.setExpanded(True)

        def show_recipe(self, recipe: Recipe) -> None:
            item_id = recipe.ItemResult.ID
            profit = self.crafting_worker.get_profit(recipe.ID)
            self.profit_value_label.setText(f"{profit:,.0f}")
            revenue = self.crafting_worker.get_revenue(item_id)
            self.revenue_value_label.setText(f"{revenue:,.1f}")
            cost = self.crafting_worker.get_aquire_cost(item_id)
            self.cost_value_label.setText(f"{cost:,.0f}")
            self.ingredients_table.clear()
            action = self.crafting_worker.get_aquire_action(item_id)
            crafting_cost = self.crafting_worker.get_crafting_cost(item_id)
            try:
                market_cost_str = (
                    f"{self.crafting_worker.get_market_cost(item_id):,.0f}"
                )
            except KeyError:
                _logger.warning(
                    f"No market cost for item {item_id} {recipe.ItemResult.Name}"
                )
                market_cost_str = ""
            item_result_row_item = QTreeWidgetItem(
                self.ingredients_table,
                [
                    recipe.ItemResult.Name,
                    action.name,
                    "",
                    f"{profit:,.0f}" if profit != np.inf else "",
                    f"{crafting_cost:,.0f}" if crafting_cost != np.inf else "",
                    market_cost_str,
                ],
            )
            self.ingredients_table.addTopLevelItem(item_result_row_item)
            self._add_recipe_to_table(recipe, item_result_row_item)
            item_result_row_item.setExpanded(True)

    retainer_listings_changed = Signal(Listings)
    classjob_level_changed = Signal(int, int)
    search_recipes = Signal(str)
    request_listings = Signal(int, int, bool)
    request_recipe = Signal(int, bool)
    # close_signal = Signal()

    def __init__(self):
        super().__init__()

        # self.gather_cost = 1000000

        # Layout
        self.main_widget = QWidget()

        self.menu_bar = QMenuBar(self)
        self.setMenuBar(self.menu_bar)
        self.item_cleaner_action = QWidgetAction(self)
        self.item_cleaner_action.setText("Item Cleaner")
        self.menu_bar.addAction(self.item_cleaner_action)
        self.item_cleaner_action.triggered.connect(self.on_item_cleaner_menu_clicked)
        self.gatherer_action = QWidgetAction(self)
        self.gatherer_action.setText("Gatherer")
        self.menu_bar.addAction(self.gatherer_action)
        self.gatherer_action.triggered.connect(self.on_gatherer_menu_clicked)

        self.main_layout = QVBoxLayout()
        self.classjob_level_layout = QHBoxLayout()
        self.main_layout.addLayout(self.classjob_level_layout)
        self.centre_splitter = QSplitter()
        self.left_splitter = QSplitter()
        self.left_splitter.setOrientation(Qt.Orientation.Vertical)
        self.right_splitter = QSplitter()
        self.right_splitter.setOrientation(Qt.Orientation.Vertical)
        self.centre_splitter.addWidget(self.left_splitter)
        self.centre_splitter.addWidget(self.right_splitter)
        self.table_search_layout = QVBoxLayout()
        self.table_search_layout.setContentsMargins(0, 0, 0, 0)
        self.table_search_widget = QWidget()

        self.search_layout = QHBoxLayout()
        # self.analyze_button = QPushButton(self)
        # self.analyze_button.setText("Analyze")
        # self.search_layout.addWidget(self.analyze_button)
        self.search_label = QLabel(self)
        self.search_label.setText("Search:")
        self.search_layout.addWidget(self.search_label)
        self.search_lineedit = QLineEdit(self)
        # self.search_lineedit.returnPressed.connect(self.on_search_return_pressed)
        self.search_layout.addWidget(self.search_lineedit)
        self.table_search_layout.addLayout(self.search_layout)

        # Xivapi manager
        self.xivapi_manager = XivapiManager(world_id)
        self._xivapi_manager_thread = QThread()
        self.xivapi_manager.moveToThread(self._xivapi_manager_thread)
        self._xivapi_manager_thread.finished.connect(self.xivapi_manager.deleteLater)
        self.classjob_level_changed.connect(
            self.xivapi_manager.set_classjob_id_level_max_slot
        )
        self.request_recipe.connect(self.xivapi_manager.request_recipe)
        self.xivapi_manager.recipe_received.connect(self.on_recipe_received)

        # Classjob level stuff!
        _logger.info("Getting classjob list...")
        classjob_list: List[ClassJob] = get_classjob_doh_list()
        self.classjob_config = PersistMapping[int, ClassJobConfig](
            "classjob_config.bin",
            {
                classjob.ID: ClassJobConfig(**classjob.dict(), level=0)
                for classjob in classjob_list
            },
        )
        self.classjob_level_layout_list = []
        for classjob_config in self.classjob_config.values():
            self.classjob_level_layout_list.append(
                _classjob_level_layout := MainWindow.ClassJobLevelLayout(
                    self, classjob_config
                )
            )
            self.classjob_level_layout.addLayout(_classjob_level_layout)
            _classjob_level_layout.joblevel_value_changed.connect(
                self.on_classjob_level_value_changed
            )
            _classjob_level_layout.joblevel_value_changed.emit(
                classjob_config.ID, classjob_config.level
            )

        self.recipe_table_model = MainWindow.RecipeTableModel(
            self, self.classjob_config
        )
        self.recipe_table_proxy_model = MainWindow.RecipeTableProxyModel(self)
        self.recipe_table_proxy_model.setSourceModel(self.recipe_table_model)
        self.recipe_table_view = MainWindow.RecipeTableView(self)
        self.recipe_table_view.setModel(self.recipe_table_proxy_model)
        self.recipe_table_view.setSortingEnabled(True)
        self.recipe_table_view.clicked.connect(self.on_table_clicked)
        self.table_search_layout.addWidget(self.recipe_table_view)
        self.search_lineedit.textChanged.connect(
            self.recipe_table_proxy_model.setFilterRegularExpression
        )

        self.table_search_widget.setLayout(self.table_search_layout)
        self.left_splitter.addWidget(self.table_search_widget)

        self.crafting_worker = CraftingWorker(
            self.xivapi_manager, self.recipe_table_view.set_profit, self
        )

        self.recipe_details = MainWindow.RecipeDetails(self.crafting_worker, self)
        self.right_splitter.addWidget(self.recipe_details)

        self.seller_id = (
            "4d9521317c92e33772cd74a166c72b0207ab9edc5eaaed5a1edb52983b70b2c2"
        )
        set_seller_id(self.seller_id)

        self.retainer_table = MainWindow.RetainerTable(self, self.seller_id)
        self.retainer_table.cellClicked.connect(self.on_retainer_table_clicked)
        self.left_splitter.addWidget(self.retainer_table)

        self.price_graph = MainWindow.PriceGraph(self)
        # self.price_graph = MainWindow.PriceGraph()
        self.right_splitter.addWidget(self.price_graph)
        self.left_splitter.setStretchFactor(0, 4)
        self.left_splitter.setStretchFactor(1, 1)
        self.right_splitter.setSizes([1, 1])
        self.right_splitter.setStretchFactor(0, 2)
        self.right_splitter.setStretchFactor(1, 1)

        self.centre_splitter.setStretchFactor(0, 5)
        self.centre_splitter.setStretchFactor(1, 4)

        self.main_layout.addWidget(self.centre_splitter)
        self.main_widget.setLayout(self.main_layout)
        self.setCentralWidget(self.main_widget)

        self.status_bar_label = QLabel()
        self.statusBar().addPermanentWidget(self.status_bar_label, 1)
        self.xivapi_manager.status_bar_set_text_signal.connect(
            self.status_bar_label.setText
        )

        self.setMinimumSize(QSize(1000, 600))

        self.universalis_manager = UniversalisManager(self.seller_id, world_id)
        self.universalis_manager.moveToThread(self._xivapi_manager_thread)
        self._xivapi_manager_thread.finished.connect(
            self.universalis_manager.deleteLater
        )
        self.universalis_manager.status_bar_set_text_signal.connect(
            self.status_bar_label.setText
        )
        self.request_listings.connect(self.universalis_manager.request_listings)
        self.universalis_manager.listings_received_signal.connect(
            self.on_listings_received
        )
        self._xivapi_manager_thread.start(QThread.LowestPriority)

        self.retainerworker_thread = QThread()
        self.retainerworker = RetainerWorker(
            seller_id=self.seller_id, world_id=world_id
        )
        self.retainerworker.moveToThread(self.retainerworker_thread)
        # self.retainerworker_thread.started.connect(self.retainerworker.run)
        self.retainerworker_thread.finished.connect(self.retainerworker.deleteLater)

        # self.crafting_worker.seller_listings_matched_signal.connect(
        #     self.retainerworker.on_retainer_listings_changed
        # )
        self.retainerworker.listing_data_updated.connect(
            self.retainer_table.on_listing_data_updated
        )

        # self.crafting_worker_thread.start(QThread.LowPriority)
        # self.crafting_worker_thread.start()
        # self.retainerworker.load_cache(
        #     self.crafting_worker.seller_listings_matched_signal
        # )
        self.retainerworker_thread.start(QThread.LowPriority)

    @Slot(int, int)
    def on_classjob_level_value_changed(
        self, classjob_id: int, classjob_level: int
    ) -> None:
        _logger.debug(f"Classjob {classjob_id} level changed to {classjob_level}")
        self.classjob_config[classjob_id].level = classjob_level
        self.classjob_level_changed.emit(classjob_id, classjob_level)
        if hasattr(self, "recipe_table_view"):
            self.recipe_table_view.classjob_level_changed()

    @Slot()
    def on_item_cleaner_menu_clicked(self) -> None:
        pass
        # form = ItemCleanerForm(self, self.crafting_worker.get_item_crafting_value_table)
        # # TODO: Connect this
        # # self.crafting_worker.crafting_value_table_changed.connect(self.form.on_crafting_value_table_changed)
        # form.show()

    @Slot()
    def on_gatherer_menu_clicked(self) -> None:
        form = GathererWindow(world_id, self)
        form.show()

    @Slot(int, int)
    def on_retainer_table_clicked(self, row: int, column: int):
        for row_group_list in self.retainer_table.table_data.values():
            for widget_list in row_group_list:
                if widget_list[0].row() != row:
                    continue
                pyperclip.copy(widget_list[2].text())
                return

    @Slot(int, int)
    def on_table_clicked(self, table_view_index: QModelIndex):
        item_name = table_view_index.siblingAtColumn(2).data()
        pyperclip.copy(item_name)
        table_data_index = self.recipe_table_proxy_model.mapToSource(table_view_index)
        recipe_id = self.recipe_table_model.table_data[table_data_index.row()][8]
        recipe: Recipe = self.xivapi_manager.request_recipe(recipe_id)
        item_id = recipe.ItemResult.ID
        if item_id in self.universalis_manager:
            self.plot_listings(self.universalis_manager[item_id])
        self.recipe_details.show_recipe(recipe)

    @Slot(Recipe)
    def on_recipe_received(self, recipe: Recipe) -> None:
        # _logger.debug(f"Recipe {recipe.ID} item result is {recipe.ItemResult.ID}")
        # t = time.time()
        self.request_listings.emit(recipe.ItemResult.ID, world_id, True)
        recipe_id = recipe.ID
        self.crafting_worker.set_recipe_id_result(recipe.ItemResult.ID, recipe_id)
        item: Item
        ingredient_recipes: List[Recipe]
        for ingredient_index in range(9):
            if item := getattr(recipe, f"ItemIngredient{ingredient_index}"):
                self.request_listings.emit(item.ID, world_id, True)
                self.crafting_worker.set_recipe_id_ingredient(item.ID, recipe_id)
                if ingredient_recipes := getattr(
                    recipe, f"ItemIngredientRecipe{ingredient_index}"
                ):
                    for ingredient_recipe in ingredient_recipes:
                        self.request_recipe.emit(ingredient_recipe.ID, True)
        self.recipe_table_view.add_recipe(recipe)
        # log_time("on_recipe_received", t, _logger)

    @Slot(Listings)
    def on_listings_received(self, listings: Listings) -> None:
        # t1 = time.time()
        item_id = listings.itemID
        # _logger.debug(f"on_listings_received: {item_id}")
        self.crafting_worker.set_revenue(item_id, get_revenue(listings))
        listing_count = len(listings.listings)
        if listing_count > 0:
            self.crafting_worker.set_market_cost(item_id, listings.minPrice)
        self.crafting_worker.queue_process_crafting_cost(item_id)
        recipe: Optional[Recipe]
        for recipe_id in self.crafting_worker.get_recipe_id_result_list(item_id):
            self.recipe_table_view.set_row_data(
                recipe_id,
                velocity=listings.regularSaleVelocity,
                listing_count=listing_count,
            )
        # Items that are ingredients
        for recipe_id in self.crafting_worker.get_recipe_id_ingredient_list(item_id):
            recipe = self.xivapi_manager.request_recipe(recipe_id)
            assert recipe is not None
            self.crafting_worker.queue_process_crafting_cost(recipe.ItemResult.ID)
        # log_time("on_listings_received", t1, _logger)

    # def process_crafting_cost(self, item_id: int) -> None:
    #     ingredient_item: Item
    #     crafting_cost: Optional[float] = np.inf
    #     _logger.debug(f"process_crafting_cost: item_id: {item_id}")
    #     for recipe_id in self.item_id_to_recipe_id_result_dict[item_id]:
    #         recipe_cost: Optional[float] = 0.0
    #         _logger.debug(f"process_crafting_cost: recipe_id: {recipe_id}")
    #         recipe = self.xivapi_manager.request_recipe(recipe_id)
    #         for ingredient_index in range(9):
    #             if ingredient_item := getattr(
    #                 recipe, f"ItemIngredient{ingredient_index}"
    #             ):
    #                 if ingredient_item.ID in self.item_id_to_aquire_action_dict:
    #                     ingredient_cost = self.item_id_to_aquire_action_dict[
    #                         ingredient_item.ID
    #                     ].aquire_cost
    #                     recipe_cost += ingredient_cost
    #                 else:
    #                     _logger.debug(
    #                         f"Cannot calculate crafting cost for {item_id}: {ingredient_item.ID} not in aquire_action_dict"
    #                     )
    #                     recipe_cost = None
    #                     break
    #         assert recipe_cost != 0.0
    #         if recipe_cost is not None:
    #             crafting_cost = min(crafting_cost, recipe_cost)
    #     _logger.debug(f"Crafting cost for {item_id}: {crafting_cost}")
    #     if crafting_cost is not None:
    #         self.item_id_to_crafting_cost_dict[item_id] = crafting_cost
    #     self.process_aquire_action(item_id)

    # def process_aquire_action(self, item_id: int) -> None:
    #     _logger.debug(
    #         f"process_aquire_action: {item_id}, crafting_cost: {self.item_id_to_crafting_cost_dict.get(item_id)}, market_cost: {self.item_id_to_market_cost_dict.get(item_id)}"
    #     )
    #     crafting_cost = self.item_id_to_crafting_cost_dict.get(item_id)
    #     market_cost = self.item_id_to_market_cost_dict.get(item_id)
    #     if market_cost is None:
    #         if crafting_cost is None:
    #             self.item_id_to_aquire_action_dict[item_id] = MainWindow.AquireAction(
    #                 MainWindow.AquireAction.AquireActionEnum.GATHER, self.gather_cost
    #             )
    #         else:
    #             self.item_id_to_aquire_action_dict[item_id] = MainWindow.AquireAction(
    #                 MainWindow.AquireAction.AquireActionEnum.CRAFT, crafting_cost
    #             )
    #     else:
    #         if crafting_cost is None:
    #             self.item_id_to_aquire_action_dict[item_id] = MainWindow.AquireAction(
    #                 MainWindow.AquireAction.AquireActionEnum.BUY, market_cost
    #             )
    #         else:
    #             if crafting_cost < market_cost:
    #                 self.item_id_to_aquire_action_dict[
    #                     item_id
    #                 ] = MainWindow.AquireAction(
    #                     MainWindow.AquireAction.AquireActionEnum.CRAFT,
    #                     market_cost,
    #                 )
    #             else:
    #                 self.item_id_to_aquire_action_dict[
    #                     item_id
    #                 ] = MainWindow.AquireAction(
    #                     MainWindow.AquireAction.AquireActionEnum.BUY,
    #                     market_cost,
    #                 )
    #     for recipe_id in self.item_id_to_recipe_id_ingredient_dict[item_id]:
    #         recipe: Recipe
    #         recipe = self.xivapi_manager.request_recipe(recipe_id)
    #         assert recipe is not None
    #         self.process_crafting_cost(recipe.ItemResult.ID)
    #     self.process_profit(item_id)

    # def process_profit(self, item_id: int) -> None:
    #     _logger.debug(f"process_profit: {item_id}")
    #     if (revenue := self.item_id_to_revenue_dict.get(item_id)) and (
    #         aquire_action := self.item_id_to_aquire_action_dict.get(item_id)
    #     ):
    #         profit = revenue - aquire_action.aquire_cost
    #         for recipe_id in self.item_id_to_recipe_id_result_dict[item_id]:
    #             self.recipe_table_view.set_row_data(recipe_id, profit=profit)

    def plot_listings(self, listings: Listings) -> None:
        self.price_graph.p1.clear()
        self.price_graph.p2.clear()
        self.price_graph.p3.clear()
        listings.history.sort_index(inplace=True)
        listings.listing_history.sort_index(inplace=True)
        self.price_graph.p1.plot(
            x=np.asarray(listings.history.index[1:]),
            y=(3600 * 24 * 7)
            / np.asarray(
                pd.Series(listings.history.index)
                - pd.Series(listings.history.index).shift(periods=1)
            )[1:],
            pen="c",
            symbol="o",
            symbolSize=5,
            symbolBrush=("c"),
        )

        if len(listings.history.index) > 2:
            # smoothing: https://stackoverflow.com/a/63511354/7552308
            # history_df = listings.history[["Price"]].apply(
            #     savgol_filter, window_length=5, polyorder=2
            # )
            # self.price_graph.p2.addItem(
            #     p2 := PlotDataItem(
            #         np.asarray(history_df.index),
            #         history_df["Price"].values,
            #         pen=self.price_graph.p1_pen,
            #         symbol="o",
            #         symbolSize=5,
            #         symbolBrush=("g"),
            #     ),
            # )
            self.price_graph.p2.addItem(
                p2 := PlotDataItem(
                    np.asarray(listings.history.index),
                    listings.history["Price"].values,
                    pen=self.price_graph.p1_pen,
                    symbol="o",
                    symbolSize=5,
                    symbolBrush=("g"),
                ),
            )

        if (
            listings.listing_history.index.size > 2
            and listings.listing_history["Price"].max()
            - listings.listing_history["Price"].min()
            > 0
        ):
            listing_history_df = listings.listing_history[
                (np.abs(stats.zscore(listings.listing_history)) < 3).all(axis=1)
            ]
            if listing_history_df.index.size != listings.listing_history.index.size:
                _logger.info("Ignoring outliers:")
                _logger.info(
                    listings.listing_history.loc[
                        listings.listing_history.index.difference(
                            listing_history_df.index
                        )
                    ]["Price"]
                )
        else:
            listing_history_df = listings.listing_history
        self.price_graph.p3.addItem(
            p3 := PlotDataItem(
                np.asarray(listing_history_df.index),
                listing_history_df["Price"].values,
                pen="m",
                symbol="o",
                symbolSize=5,
                symbolBrush=("m"),
            ),
        )
        # p3.setLogMode(False, True)
        self.price_graph.auto_range()

    def closeEvent(self, event):
        print("exiting ui...")
        # self.crafting_worker_thread.setPriority(QThread.NormalPriority)
        # self.crafting_worker.stop()
        # self.crafting_worker_thread.quit()
        # self.crafting_worker_thread.wait()
        # print("crafting worker closed")

        # self._xivapi_manager_thread.
        # self.xivapi_manager.moveToThread(QThread.currentThread())
        # self.close_signal.connect(
        #     self.xivapi_manager.save_to_disk, Qt.BlockingQueuedConnection
        # )
        # self.close_signal.emit()
        self._xivapi_manager_thread.quit()
        self._xivapi_manager_thread.wait()
        # self.xivapi_manager.moveToThread(QThread.currentThread())
        self.xivapi_manager.save_to_disk()
        print("xivapi saved")

        self.universalis_manager.save_to_disk()
        print("universalis saved")

        self.retainerworker_thread.quit()
        self.retainerworker_thread.wait()
        print("retainer worker closed")
        self.classjob_config.save_to_disk()
        print("classjob config saved")
        universalis_save_to_disk()
        print("universalis saved")
        xivapi_save_to_disk()
        print("xivapi saved")
        self.retainerworker.save_cache()
        print("retainer cache saved")
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication([])

    main_window = MainWindow()
    main_window.show()

    app.exec()

# Ideas:
# Better caching of persistent data
# look for matching retainers when pulling all data, not just in a few loops
