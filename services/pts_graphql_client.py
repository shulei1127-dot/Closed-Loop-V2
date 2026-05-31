"""PTS GraphQL API client (Python port of pts-api.ts)

Uses API-token Bearer authentication to call PTS GraphQL endpoints.
PTS API token mode does not support GraphQL variable parameters ($var),
so variables are inlined into the query string before sending.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Rate limit: max ~5 requests/second
_RATE_LIMIT_INTERVAL_MS = 250


class _EnumMarker:
    """Wrapper so inline_variables emits the value without quotes (GraphQL enum)."""

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value


def enum_value(value: str) -> _EnumMarker:
    """Mark a value as a GraphQL enum so inline_variables omits quotes.

    Example::

        client.inline_variables(query, {"related_type": enum_value("product_delivery")})
        # -> related_type: product_delivery  (no quotes)
    """
    return _EnumMarker(value)


def _inline_list(items: list[Any]) -> str:
    """Convert a Python list to a GraphQL list literal.

    Strings are quoted, enums are unquoted, numbers are bare.
    """
    elements: list[str] = []
    for item in items:
        if isinstance(item, _EnumMarker):
            elements.append(item.value)
        elif isinstance(item, bool):
            elements.append(str(item).lower())
        elif isinstance(item, (int, float)):
            elements.append(str(item))
        elif isinstance(item, str):
            elements.append(f'"{item}"')
        else:
            elements.append(f'"{item}"')
    return "[" + ", ".join(elements) + "]"


# ---------------------------------------------------------------------------
# GraphQL query strings (sourced from browser/api-endpoints.ts)
# ---------------------------------------------------------------------------

QUERY_ME = """{
  me {
    id
    name
    __typename
  }
}"""

QUERY_DELIVERY_BY_ID = """query DeliveryById($id: ID!) {
  product_delivery_by_id(id: $id) {
    id
    project {
      id
      name
      delivery_type
      company {
        id
        name
        contact {
          id
          name
          phone
          email
          duty
          __typename
        }
        __typename
      }
      __typename
    }
    delivery_status
    product_delivery_project {
      products {
        product {
          id
          name
          group
          __typename
        }
        form {
          id
          name
          __typename
        }
        infos {
          version {
            name
            module_groups {
              name
              modules {
                name
                number
                __typename
              }
              __typename
            }
            __typename
          }
          number
          __typename
        }
        __typename
      }
      __typename
    }
    item_list {
      id
      product_detail {
        product {
          id
          name
          group
          __typename
        }
        form {
          id
          name
          __typename
        }
        __typename
      }
      end_at
      over_guarantee
      work_hour
      valid
      __typename
    }
    after_sale {
      id
      name
      username
      __typename
    }
    assigner {
      id
      name
      username
      __typename
    }
    person_in_charge {
      id
      name
      username
      __typename
    }
    product_info {
      id
      type
      product_detail {
        product {
          id
          name
          __typename
        }
        form {
          id
          name
          __typename
        }
        __typename
      }
      delivery {
        id
        project {
          id
          name
          company {
            id
            name
            __typename
          }
          __typename
        }
        __typename
      }
      desc
      number
      __typename
    }
    contact_list {
      return_visit
      contact {
        id
        name
        phone
        email
        duty
        __typename
      }
      __typename
    }
    __typename
  }
}"""

QUERY_PRODUCT_INFO_BY_ID = """query ProductInfoByID($id: ID!) {
  productInfoByID(id: $id) {
    id
    type
    desc
    number
    over_guarantee
    product_detail {
      product {
        id
        name
        __typename
      }
      form {
        id
        name
        __typename
      }
      __typename
    }
    after_info {
      type_number
      serial_number
      machine_code
      stage_mode
      product_version
      engine_version
      is_ha
      license_nature
      license_validity
      license_id
      after_sales_validity
      rank
      desc
      note
      needle_version
      needle_number
      patch_version
      __typename
    }
    __typename
  }
}"""

QUERY_PENDING_DELIVERY_LIST = """{
  list_product_delivery(
    search: { delivery_status: to_after_sale_review, after_sale: $after_sale_ids },
    pagination: { skip: 0, limit: 100 },
    SortBy: { by: "id", sort: 1 }
  ) {
    total
    data {
      id
      project {
        id
        name
        company {
          id
          name
          __typename
        }
        __typename
      }
      delivery_status
      after_sale {
        id
        name
        username
        __typename
      }
      assigner {
        name
        username
        __typename
      }
      person_in_charge {
        name
        username
        __typename
      }
      __typename
    }
  }
}"""

QUERY_RELATED_DELIVERY_TASK_LIST = """query RelatedDeliveryTaskList($related_type: Target!, $related_id: String!) {
  related_delivery_task_list(related_type: $related_type, related_id: $related_id) {
    id
    index
    task_template
    task_type
    related_type
    status
    start_at
    end_at
    task_person {
      id
      name
      username
      __typename
    }
    __typename
  }
}"""

QUERY_DELIVERY_TASK_BY_ID = """query DeliveryTask($id: String!) {
  delivery_task(id: $id) {
    id
    task_type
    status
    finished_at
    task_person {
      id
      name
      username
      __typename
    }
    reviewer {
      id
      name
      username
      __typename
    }
    comment {
      id
      content
      create_type
      created_at
      creator {
        id
        name
        username
        __typename
      }
      __typename
    }
    __typename
  }
}"""


class PtsGraphQLClient:
    """Async GraphQL client for the PTS API.

    Parameters
    ----------
    api_base_url:
        PTS internal API base URL (e.g. ``http://api.in.chaitin.net``).
    api_token:
        Bearer token for authentication.  When provided the GraphQL endpoint
        path is ``/pts/query`` (internal API); otherwise ``/query`` (web).
    """

    def __init__(self, api_base_url: str, api_token: str = "") -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.api_token = api_token
        self._graphql_path = "/pts/query" if api_token else "/query"
        self._last_call_time: float = 0.0

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    async def _rate_limit(self) -> None:
        """Ensure at least _RATE_LIMIT_INTERVAL_MS between consecutive calls."""
        now = time.monotonic()
        elapsed_ms = (now - self._last_call_time) * 1000
        if elapsed_ms < _RATE_LIMIT_INTERVAL_MS:
            await asyncio.sleep((_RATE_LIMIT_INTERVAL_MS - elapsed_ms) / 1000)
        self._last_call_time = time.monotonic()

    # ------------------------------------------------------------------
    # Variable inlining
    # ------------------------------------------------------------------

    @staticmethod
    def inline_variables(query: str, variables: dict[str, Any] | None = None) -> str:
        """Replace ``$variable`` references in *query* with literal values.

        PTS API-token mode does not support GraphQL variable parameters, so all
        variables must be inlined before sending the query.

        * String values are wrapped in double-quotes.
        * Numeric / boolean values are emitted as-is.
        * Values wrapped with :func:`enum_value` are emitted **without** quotes.
        * The operation-level variable declaration (e.g.
          ``query Xxx($id: ID!, ...)``) is stripped.

        Variable names are replaced longest-first to avoid partial replacement
        (e.g. ``$related`` accidentally matching inside ``$related_id``).
        """
        if not variables:
            return query

        result = query

        # Strip operation variable declaration: query Xxx($id: ID!, $type: Target!) -> query Xxx
        result = re.sub(
            r"\((\$\w+:\s*\w+!?\s*,?\s*)+\)",
            "",
            result,
        )

        # Sort by variable name length descending to avoid partial replacement
        entries = sorted(variables.items(), key=lambda kv: len(kv[0]), reverse=True)

        for key, value in entries:
            var_ref = f"${key}"

            # Build the literal representation
            if isinstance(value, _EnumMarker):
                literal = value.value
            elif isinstance(value, list):
                literal = _inline_list(value)
            elif isinstance(value, bool):
                # Must check before int because bool is subclass of int
                literal = str(value).lower()
            elif isinstance(value, (int, float)):
                literal = str(value)
            elif isinstance(value, str):
                literal = f'"{value}"'
            else:
                literal = f'"{value}"'

            # Replace $varname only when NOT followed by a word character
            result = re.sub(
                re.escape(var_ref) + r"(?![\w])",
                literal,
                result,
            )

        return result

    # ------------------------------------------------------------------
    # Core query method
    # ------------------------------------------------------------------

    async def query(
        self,
        query_str: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a GraphQL query and return the ``data`` field.

        Raises
        ------
        RuntimeError
            * ``SESSION_EXPIRED`` on HTTP 401.
            * Rate-limit error on HTTP 429.
            * Generic API error on other non-2xx responses.
            * ``GRAPHQL_ERROR`` when the response contains GraphQL errors.
        """
        await self._rate_limit()

        inlined = self.inline_variables(query_str, variables)

        async with httpx.AsyncClient(
            base_url=self.api_base_url,
            verify=False,
        ) as client:
            resp = await client.post(
                self._graphql_path,
                json={"query": inlined},
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_token}",
                },
            )

        if resp.status_code == 401:
            raise RuntimeError("SESSION_EXPIRED: API 令牌无效或已过期")
        if resp.status_code == 429:
            raise RuntimeError("API_ERROR: 请求过于频繁，请稍后再试")
        if resp.status_code >= 400:
            raise RuntimeError(f"API_ERROR: PTS API 返回 {resp.status_code}")

        body = resp.json()
        if body.get("errors"):
            first_msg = body["errors"][0].get("message", "") if body["errors"] else ""
            raise RuntimeError(
                f"GRAPHQL_ERROR: {first_msg or str(body['errors'])}"
            )

        return body.get("data", {})

    async def try_query(
        self,
        query_str: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Attempt a GraphQL query, returning ``None`` on failure instead of raising."""
        try:
            return await self.query(query_str, variables)
        except Exception as exc:
            logger.debug("PTS GraphQL 查询失败: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Convenience query methods
    # ------------------------------------------------------------------

    async def query_me(self) -> dict[str, Any] | None:
        """Fetch current user info (validates session)."""
        return await self.try_query(QUERY_ME)

    async def query_delivery_by_id(self, project_id: str) -> dict[str, Any] | None:
        """Fetch full delivery/project data by project ID."""
        return await self.try_query(
            QUERY_DELIVERY_BY_ID,
            {"id": project_id},
        )

    async def query_product_info_by_id(self, product_id: str) -> dict[str, Any] | None:
        """Fetch product detail (including after_info metadata)."""
        return await self.try_query(
            QUERY_PRODUCT_INFO_BY_ID,
            {"id": product_id},
        )

    async def query_pending_delivery_list(
        self, *, after_sale_ids: list[str] | None = None
    ) -> dict[str, Any] | None:
        """Fetch pending projects with delivery_status=to_after_sale_review.

        Parameters
        ----------
        after_sale_ids:
            PTS user IDs to filter by after_sale field. When ``None`` the
            ``after_sale`` filter is omitted entirely, returning ALL projects
            in the to_after_sale_review stage. Pass a list of IDs to restrict
            results to specific after-sale responsible persons.
        """
        if after_sale_ids is not None:
            variables = {"after_sale_ids": after_sale_ids}
        else:
            # Remove the after_sale filter entirely so all to_after_sale_review
            # projects are returned regardless of after-sale assignment.
            query = QUERY_PENDING_DELIVERY_LIST.replace(", after_sale: $after_sale_ids", "")
            return await self.try_query(query)

        return await self.try_query(QUERY_PENDING_DELIVERY_LIST, variables)

    async def query_related_delivery_task_list(
        self, related_id: str
    ) -> dict[str, Any] | None:
        """Fetch related delivery tasks for a given ID (related_type=product_delivery)."""
        return await self.try_query(
            QUERY_RELATED_DELIVERY_TASK_LIST,
            {
                "related_type": enum_value("product_delivery"),
                "related_id": related_id,
            },
        )

    async def query_delivery_task_by_id(self, task_id: str) -> dict[str, Any] | None:
        """Fetch task detail including comment history."""
        return await self.try_query(
            QUERY_DELIVERY_TASK_BY_ID,
            {"id": task_id},
        )
