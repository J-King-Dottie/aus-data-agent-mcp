create extension if not exists pgcrypto;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop table if exists public.model_run_assumptions cascade;
drop table if exists public.model_run_variables cascade;
drop table if exists public.model_exports cascade;
drop table if exists public.model_runs cascade;
drop table if exists public.model_edges cascade;
drop table if exists public.model_nodes cascade;

create table if not exists public.modelling_projects (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null default 'Untitled model',
  question text not null default '',
  status text not null default 'draft' check (status in ('draft', 'active', 'archived')),
  conversation_id text not null,
  model_builder_state jsonb not null default '{}'::jsonb,
  model_graph_state jsonb not null default '{"nodes":[],"edges":[]}'::jsonb,
  node_data jsonb not null default '{}'::jsonb,
  active_validated_variable_ids jsonb not null default '[]'::jsonb,
  memory_text text not null default '',
  last_compacted_conversation_id text not null default '',
  last_compacted_message_count integer not null default 0,
  last_compacted_created_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, conversation_id)
);

alter table public.modelling_projects
  add column if not exists model_builder_state jsonb not null default '{}'::jsonb;

do $$
begin
  if exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'modelling_projects'
      and column_name = 'model_calculated_data'
  )
  and not exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'modelling_projects'
      and column_name = 'node_data'
  ) then
    alter table public.modelling_projects rename column model_calculated_data to node_data;
  end if;
end $$;

alter table public.modelling_projects
  add column if not exists model_graph_state jsonb not null default '{"nodes":[],"edges":[]}'::jsonb,
  add column if not exists node_data jsonb not null default '{}'::jsonb,
  add column if not exists active_validated_variable_ids jsonb not null default '[]'::jsonb,
  add column if not exists memory_text text not null default '',
  add column if not exists last_compacted_conversation_id text not null default '',
  add column if not exists last_compacted_message_count integer not null default 0,
  add column if not exists last_compacted_created_at timestamptz;

do $$
begin
  update public.modelling_projects mp
  set active_validated_variable_ids = '[]'::jsonb;

  update public.modelling_projects mp
  set model_builder_state = jsonb_set(
        jsonb_set(
          jsonb_set(
            coalesce(mp.model_builder_state, '{}'::jsonb),
            '{variables}',
            '[]'::jsonb,
            true
          ),
          '{nodes}',
          '[]'::jsonb,
          true
        ),
        '{edges}',
        '[]'::jsonb,
        true
       ),
      model_graph_state = '{"nodes":[],"edges":[]}'::jsonb;

  update public.modelling_projects mp
  set model_graph_state = jsonb_build_object(
        'nodes', coalesce(mp.model_builder_state->'nodes', '[]'::jsonb),
        'edges', coalesce(mp.model_builder_state->'edges', '[]'::jsonb)
      )
  where mp.model_graph_state = '{"nodes":[],"edges":[]}'::jsonb
    and (
      jsonb_typeof(mp.model_builder_state->'nodes') = 'array'
      or jsonb_typeof(mp.model_builder_state->'edges') = 'array'
    );

  update public.modelling_projects mp
  set active_validated_variable_ids = coalesce(
        (
          select jsonb_agg(variable->>'id')
          from jsonb_array_elements(coalesce(mp.model_builder_state->'variables', '[]'::jsonb)) variable
          where coalesce(variable->>'id', '') <> ''
            and coalesce(variable->>'validationStatus', 'validated') = 'validated'
        ),
        mp.active_validated_variable_ids,
        '[]'::jsonb
      )
  where mp.active_validated_variable_ids = '[]'::jsonb
    and jsonb_typeof(mp.model_builder_state->'variables') = 'array';

  if to_regclass('public.project_validated_variables') is not null then
    update public.modelling_projects mp
    set active_validated_variable_ids = coalesce(
      (
        select jsonb_agg(pvv.variable_id::text order by pvv.created_at)
        from public.project_validated_variables pvv
        where pvv.project_id = mp.id and pvv.active
      ),
      mp.active_validated_variable_ids,
      '[]'::jsonb
    );
  end if;

  if to_regclass('public.agent_project_memory') is not null then
    update public.modelling_projects mp
    set memory_text = apm.memory_text,
        last_compacted_conversation_id = apm.last_compacted_conversation_id,
        last_compacted_message_count = apm.last_compacted_message_count,
        last_compacted_created_at = apm.last_compacted_created_at
    from public.agent_project_memory apm
    where apm.project_id = mp.id and apm.user_id = mp.user_id;
  end if;
end;
$$;

drop table if exists public.project_validated_variables cascade;
drop table if exists public.agent_project_memory cascade;

drop trigger if exists set_modelling_projects_updated_at on public.modelling_projects;
create trigger set_modelling_projects_updated_at
before update on public.modelling_projects
for each row execute function public.set_updated_at();

create table if not exists public.modelling_chat_messages (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  project_id uuid not null references public.modelling_projects(id) on delete cascade,
  conversation_id text not null,
  run_index integer not null default 0,
  user_message text not null default '',
  progress_notes jsonb not null default '[]'::jsonb,
  final_response text not null default '',
  final_response_format text not null default 'markdown',
  run_cost jsonb,
  status text not null default 'completed',
  response_time_secs numeric,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

do $$
begin
  if exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'modelling_chat_messages'
      and column_name = 'message_index'
  ) then
    delete from public.modelling_chat_messages;
  end if;
end;
$$;

alter table public.modelling_chat_messages
  drop constraint if exists modelling_chat_messages_project_id_conversation_id_message_index_key;

alter table public.modelling_chat_messages
  drop constraint if exists modelling_chat_messages_project_conversation_run_key;

alter table public.modelling_chat_messages
  drop column if exists parent_message_id,
  drop column if exists message_index,
  drop column if exists role,
  drop column if exists content,
  add column if not exists run_index integer not null default 0,
  add column if not exists user_message text not null default '',
  add column if not exists progress_notes jsonb not null default '[]'::jsonb,
  add column if not exists final_response text not null default '',
  add column if not exists final_response_format text not null default 'markdown',
  add column if not exists status text not null default 'completed',
  add column if not exists response_time_secs numeric;

drop trigger if exists set_modelling_chat_messages_updated_at on public.modelling_chat_messages;
create trigger set_modelling_chat_messages_updated_at
before update on public.modelling_chat_messages
for each row execute function public.set_updated_at();

create table if not exists public.ai_usage (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  project_id uuid not null references public.modelling_projects(id) on delete cascade,
  chat_message_id uuid unique references public.modelling_chat_messages(id) on delete set null,
  conversation_id text not null,
  run_index integer not null default 0,
  model text not null default '',
  input_tokens integer not null default 0,
  cached_input_tokens integer not null default 0,
  output_tokens integer not null default 0,
  ai_cost_usd numeric(12, 6) not null default 0,
  surcharge_usd numeric(12, 6) not null default 0,
  final_cost_usd numeric(12, 6) not null default 0,
  pricing jsonb not null default '{}'::jsonb,
  usage_payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.validated_variables (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  project_id uuid references public.modelling_projects(id) on delete set null,
  origin_project_id uuid references public.modelling_projects(id) on delete set null,
  external_key text,
  name text not null,
  label text not null default '',
  source_name text not null default '',
  provider_id text not null default '',
  dataset_id text not null default '',
  metric text not null default '',
  unit text not null default '',
  geography text not null default '',
  frequency text not null default '',
  seasonal_treatment text not null default '',
  period_start text not null default '',
  period_end text not null default '',
  validation_status text not null default 'candidate' check (validation_status in ('candidate', 'validated', 'rejected')),
  validated_api_url text not null default '',
  transform_summary text not null default '',
  node_description text not null default '',
  contents_summary text not null default '',
  validated_data jsonb not null default '{}'::jsonb,
  refresh_code text not null default '',
  approved_by uuid references auth.users(id) on delete set null,
  approved_at timestamptz,
  created_from_message_id uuid references public.modelling_chat_messages(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.validated_variables
  add column if not exists origin_project_id uuid;

alter table public.validated_variables
  add column if not exists node_description text not null default '',
  add column if not exists contents_summary text not null default '';

alter table public.validated_variables
  add column if not exists validated_api_url text not null default '';

alter table public.validated_variables
  add column if not exists validated_data jsonb not null default '{}'::jsonb;

alter table public.validated_variables
  add column if not exists refresh_code text not null default '';

alter table public.validated_variables
  drop column if exists retrieval_logic,
  drop column if exists transformation_logic,
  drop column if exists refresh_metadata,
  drop column if exists evidence_artifact,
  drop column if exists recreation_summary;

update public.validated_variables
set origin_project_id = project_id
where origin_project_id is null and project_id is not null;

alter table public.validated_variables
  alter column project_id drop not null;

alter table public.validated_variables
  drop constraint if exists validated_variables_project_id_fkey;

alter table public.validated_variables
  add constraint validated_variables_project_id_fkey
  foreign key (project_id) references public.modelling_projects(id) on delete set null;

alter table public.validated_variables
  drop constraint if exists validated_variables_origin_project_id_fkey;

alter table public.validated_variables
  add constraint validated_variables_origin_project_id_fkey
  foreign key (origin_project_id) references public.modelling_projects(id) on delete set null;

drop trigger if exists set_validated_variables_updated_at on public.validated_variables;
create trigger set_validated_variables_updated_at
before update on public.validated_variables
for each row execute function public.set_updated_at();

create index if not exists modelling_projects_user_updated_idx
  on public.modelling_projects(user_id, updated_at desc);

drop index if exists public.modelling_chat_messages_project_conversation_idx;

create unique index if not exists modelling_chat_messages_project_conversation_run_idx
  on public.modelling_chat_messages(project_id, conversation_id, run_index);

create index if not exists ai_usage_user_project_created_idx
  on public.ai_usage(user_id, project_id, created_at desc);

create index if not exists ai_usage_project_conversation_idx
  on public.ai_usage(project_id, conversation_id, run_index);

insert into public.ai_usage
  (user_id, project_id, chat_message_id, conversation_id, run_index, model,
   input_tokens, cached_input_tokens, output_tokens,
   ai_cost_usd, surcharge_usd, final_cost_usd, pricing, usage_payload, created_at)
select
  mcm.user_id,
  mcm.project_id,
  mcm.id,
  mcm.conversation_id,
  mcm.run_index,
  coalesce(mcm.run_cost->>'model', ''),
  case when coalesce(mcm.run_cost->>'input_tokens', '') ~ '^[0-9]+(\\.[0-9]+)?$'
    then greatest((mcm.run_cost->>'input_tokens')::numeric::integer, 0) else 0 end,
  case when coalesce(mcm.run_cost->>'cached_input_tokens', '') ~ '^[0-9]+(\\.[0-9]+)?$'
    then greatest((mcm.run_cost->>'cached_input_tokens')::numeric::integer, 0) else 0 end,
  case when coalesce(mcm.run_cost->>'output_tokens', '') ~ '^[0-9]+(\\.[0-9]+)?$'
    then greatest((mcm.run_cost->>'output_tokens')::numeric::integer, 0) else 0 end,
  case when coalesce(mcm.run_cost->>'ai_cost_usd', '') ~ '^[0-9]+(\\.[0-9]+)?$'
    then (mcm.run_cost->>'ai_cost_usd')::numeric else 0 end,
  case when coalesce(mcm.run_cost->>'surcharge_usd', '') ~ '^[0-9]+(\\.[0-9]+)?$'
    then (mcm.run_cost->>'surcharge_usd')::numeric else 0 end,
  case when coalesce(mcm.run_cost->>'final_cost_usd', '') ~ '^[0-9]+(\\.[0-9]+)?$'
    then (mcm.run_cost->>'final_cost_usd')::numeric else 0 end,
  coalesce(mcm.run_cost->'pricing', '{}'::jsonb),
  mcm.run_cost,
  mcm.created_at
from public.modelling_chat_messages mcm
where mcm.run_cost is not null
  and jsonb_typeof(mcm.run_cost) = 'object'
on conflict (chat_message_id) do nothing;

create index if not exists validated_variables_project_status_idx
  on public.validated_variables(project_id, validation_status, created_at);

drop index if exists public.validated_variables_project_external_key_idx;

create unique index if not exists validated_variables_user_external_key_idx
  on public.validated_variables(user_id, external_key);

alter table public.modelling_projects enable row level security;
alter table public.modelling_chat_messages enable row level security;
alter table public.ai_usage enable row level security;
alter table public.validated_variables enable row level security;

drop policy if exists "Users manage own modelling projects" on public.modelling_projects;
create policy "Users manage own modelling projects"
  on public.modelling_projects
  for all
  to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

drop policy if exists "Users manage own modelling chat messages" on public.modelling_chat_messages;
create policy "Users manage own modelling chat messages"
  on public.modelling_chat_messages
  for all
  to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

drop policy if exists "Users manage own AI usage" on public.ai_usage;
create policy "Users manage own AI usage"
  on public.ai_usage
  for all
  to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

grant select, insert, update, delete on public.ai_usage to authenticated;

drop policy if exists "Users manage own validated variables" on public.validated_variables;
create policy "Users manage own validated variables"
  on public.validated_variables
  for all
  to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
