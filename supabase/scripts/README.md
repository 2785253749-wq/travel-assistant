# Community administrator initialization

Generate the idempotent SQL for an existing Supabase Auth user:

```text
python supabase/scripts/initialize_community_admin.py --user-id 00000000-0000-0000-0000-000000000000
```

Run the generated statement with a service-role database connection. The
script never grants `anon` access and does not embed an administrator UUID.
