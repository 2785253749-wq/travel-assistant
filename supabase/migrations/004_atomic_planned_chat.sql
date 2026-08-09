create function public.persist_planned_chat(
  p_create boolean,
  p_trip_id uuid,
  p_title text,
  p_profile jsonb,
  p_itinerary jsonb,
  p_user_message_id uuid,
  p_user_message text,
  p_assistant_message_id uuid,
  p_assistant_message text
) returns setof public.trips
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_user_id uuid := auth.uid();
  v_trip public.trips;
begin
  if v_user_id is null then
    raise exception 'authentication required' using errcode = '42501';
  end if;

  if p_create then
    insert into public.trips (id, user_id, title, status, profile, itinerary)
    values (p_trip_id, v_user_id, p_title, 'planned', p_profile, p_itinerary)
    returning * into v_trip;
  else
    update public.trips
      set title = p_title,
          status = 'planned',
          profile = p_profile,
          itinerary = p_itinerary,
          updated_at = now()
      where id = p_trip_id and user_id = v_user_id
      returning * into v_trip;
    if not found then
      raise exception 'trip not found' using errcode = 'P0002';
    end if;
  end if;

  insert into public.conversation_messages (id, user_id, trip_id, role, content)
  values (p_user_message_id, v_user_id, v_trip.id, 'user', p_user_message);

  insert into public.conversation_messages (id, user_id, trip_id, role, content)
  values (p_assistant_message_id, v_user_id, v_trip.id, 'assistant', p_assistant_message);

  return query select * from public.trips where id = v_trip.id and user_id = v_user_id;
end
$$;

revoke all on function public.persist_planned_chat(
  boolean, uuid, text, jsonb, jsonb, uuid, text, uuid, text
) from public;
grant execute on function public.persist_planned_chat(
  boolean, uuid, text, jsonb, jsonb, uuid, text, uuid, text
) to authenticated;
