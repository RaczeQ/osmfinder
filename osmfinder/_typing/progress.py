"""Progress bar types."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class ProgressBar(Protocol):
    """
    Progress bar object accepted by the `progressbar` arguments.

    It follows the same contract as the `progressbar` argument of the `pooch` library. For every
    downloaded file, `total` is set to the file size in bytes, `update` is called with the number
    of downloaded bytes, and `reset`, `update` and `close` are called at the end. If the object
    also has a `set_description` method (like `tqdm`), it is called with the file name before
    each download.

    The object must be truthy, because `pooch` checks it before setting `total`. A `tqdm` bar
    needs a non-zero `total`, for example `tqdm(total=1)`.
    """

    total: int | float | None

    def update(self, n: int, /) -> object:
        """Advance the progress by `n` bytes."""

    def reset(self) -> object:
        """Reset the progress before it is refilled at the end of a download."""

    def close(self) -> object:
        """Finish the download."""


# Show a default `tqdm` progress bar (`True`), nothing (`False`) or a custom progress bar.
ProgressBarLike = bool | ProgressBar
