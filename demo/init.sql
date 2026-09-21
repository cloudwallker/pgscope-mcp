\set ON_ERROR_STOP on

-- This file runs only on first initialization of the Compose PostgreSQL volume.
REVOKE ALL ON DATABASE pgscope_demo FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;

CREATE TABLE public.customers (
    id bigint PRIMARY KEY,
    name text NOT NULL,
    email text NOT NULL UNIQUE,
    region text NOT NULL
);

CREATE TABLE public.orders (
    id bigint PRIMARY KEY,
    customer_id bigint NOT NULL REFERENCES public.customers(id),
    created_at timestamp without time zone NOT NULL,
    status text NOT NULL,
    total numeric(12, 2) NOT NULL
);

CREATE TABLE public.order_items (
    id bigint PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES public.orders(id),
    sku text NOT NULL,
    quantity integer NOT NULL,
    unit_price numeric(12, 2) NOT NULL
);

INSERT INTO public.customers (id, name, email, region)
SELECT g, 'Customer ' || g, 'customer' || g || '@example.test',
       (ARRAY['north', 'south', 'east', 'west'])[(g % 4) + 1]
FROM generate_series(1, 5000) AS g;

INSERT INTO public.orders (id, customer_id, created_at, status, total)
SELECT g, ((g - 1) % 5000) + 1,
       timestamp '2024-01-01 00:00:00' + ((g - 1) % 365) * interval '1 day'
           + ((g - 1) % 1440) * interval '1 minute',
       (ARRAY['new', 'paid', 'shipped', 'cancelled'])[(g % 4) + 1],
       (((g % 19999) + 100)::numeric / 100)::numeric(12, 2)
FROM generate_series(1, 100000) AS g;

INSERT INTO public.order_items (id, order_id, sku, quantity, unit_price)
SELECT g, ((g - 1) / 2) + 1,
       'SKU-' || lpad(((g - 1) % 1000 + 1)::text, 4, '0'),
       ((g - 1) % 5) + 1,
       (((g % 9999) + 100)::numeric / 100)::numeric(12, 2)
FROM generate_series(1, 200000) AS g;

-- The marker is deliberately outside the three business tables and is admin-only.
CREATE TABLE public.pgscope_demo_marker (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    marker text NOT NULL CHECK (marker = 'pgscope-demo-v1')
);
INSERT INTO public.pgscope_demo_marker (marker) VALUES ('pgscope-demo-v1');

CREATE ROLE pgscope_reader LOGIN PASSWORD 'pgscope_demo_readonly'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
ALTER ROLE pgscope_reader SET default_transaction_read_only = on;
GRANT CONNECT ON DATABASE pgscope_demo TO pgscope_reader;
GRANT USAGE ON SCHEMA public TO pgscope_reader;
GRANT SELECT ON public.customers, public.orders, public.order_items TO pgscope_reader;
REVOKE TEMPORARY ON DATABASE pgscope_demo FROM PUBLIC, pgscope_reader;
REVOKE CREATE ON SCHEMA public FROM PUBLIC, pgscope_reader;

ANALYZE public.customers;
ANALYZE public.orders;
ANALYZE public.order_items;
