from django.core.paginator import Paginator
from django.utils.functional import cached_property
from rest_framework.pagination import PageNumberPagination


class WireCountPaginator(Paginator):
    """Use a request-precomputed count when the canonical Wire rank is known."""

    @cached_property
    def count(self):
        fast_count = getattr(self.object_list, "_wire_fast_count", None)
        if fast_count is not None:
            return int(fast_count)
        return super().count



class FlexiblePagination(PageNumberPagination):
    django_paginator_class = WireCountPaginator
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 500
