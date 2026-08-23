"""Print an idempotent SQL statement for granting the community admin role.

The script deliberately does not contain a user UUID. The operator must pass
the already-created Supabase Auth user's UUID explicitly.
"""

from __future__ import annotations

import argparse
from uuid import UUID


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize a community administrator")
    parser.add_argument(
        "--user-id",
        required=True,
        type=UUID,
        help="UUID of an existing auth.users record",
    )
    args = parser.parse_args()
    print(
        "insert into public.user_roles (user_id, role)\n"
        f"values ('{args.user_id}'::uuid, 'admin')\n"
        "on conflict (user_id, role) do nothing;"
    )


if __name__ == "__main__":
    main()
