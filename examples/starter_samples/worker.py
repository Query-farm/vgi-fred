"""Run original starter samples for regression tests."""

from vgi import Worker
from vgi.catalog import Catalog, Schema

from .fetch_items import API_SECRET_TYPE, FetchItems
from .greet import Greet, GreetWithStyle
from .sequence import SequenceTable


class RecipeWorker(Worker):
    """Original starter catalog, independent from the FRED API worker."""

    catalog = Catalog(
        name="fred", schemas=[Schema(path=["main"], functions=[Greet, GreetWithStyle, SequenceTable, FetchItems])]
    )
    secret_types = [API_SECRET_TYPE]


if __name__ == "__main__":
    RecipeWorker.main()
