CREATE TABLE ai_reports (
	id SERIAL NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	period VARCHAR(64), 
	question TEXT, 
	summary TEXT NOT NULL, 
	digest JSONB, 
	model VARCHAR(64), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE channels (
	id SERIAL NOT NULL, 
	platform VARCHAR(50) NOT NULL, 
	external_id VARCHAR(255), 
	title VARCHAR(255), 
	url VARCHAR(500), 
	meta JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (platform, external_id)
);

CREATE TABLE funnel_stages (
	id SERIAL NOT NULL, 
	key VARCHAR(50) NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	ordering INTEGER NOT NULL, 
	stage_type VARCHAR(50), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (key)
);

CREATE TABLE sync_runs (
	id SERIAL NOT NULL, 
	connector VARCHAR(50) NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(32) NOT NULL, 
	stats JSONB, 
	error TEXT, 
	PRIMARY KEY (id)
);

CREATE TABLE tariffs (
	id SERIAL NOT NULL, 
	key VARCHAR(50) NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	price NUMERIC(12, 2), 
	currency VARCHAR(8), 
	meta JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (key)
);

CREATE TABLE content (
	id SERIAL NOT NULL, 
	channel_id INTEGER NOT NULL, 
	external_id VARCHAR(255) NOT NULL, 
	type VARCHAR(50), 
	title VARCHAR(500), 
	url VARCHAR(500), 
	published_at TIMESTAMP WITH TIME ZONE, 
	metrics JSONB, 
	raw JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (channel_id, external_id), 
	FOREIGN KEY(channel_id) REFERENCES channels (id)
);

CREATE TABLE funnel_steps (
	id SERIAL NOT NULL, 
	channel_id INTEGER, 
	bot_referral VARCHAR(255) NOT NULL, 
	external_id VARCHAR(255) NOT NULL, 
	title VARCHAR(500) NOT NULL, 
	stage_id INTEGER, 
	tariff_id INTEGER, 
	role VARCHAR(50), 
	ordering INTEGER NOT NULL, 
	raw JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (bot_referral, external_id), 
	FOREIGN KEY(channel_id) REFERENCES channels (id), 
	FOREIGN KEY(stage_id) REFERENCES funnel_stages (id), 
	FOREIGN KEY(tariff_id) REFERENCES tariffs (id)
);

CREATE TABLE raw_records (
	id SERIAL NOT NULL, 
	source_system VARCHAR(50) NOT NULL, 
	entity_type VARCHAR(50) NOT NULL, 
	external_id VARCHAR(255) NOT NULL, 
	payload JSONB NOT NULL, 
	run_id INTEGER, 
	fetched_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (source_system, entity_type, external_id), 
	FOREIGN KEY(run_id) REFERENCES sync_runs (id)
);

CREATE TABLE sources (
	id SERIAL NOT NULL, 
	channel_id INTEGER, 
	kind VARCHAR(50) NOT NULL, 
	code VARCHAR(500) NOT NULL, 
	utm_source VARCHAR(255), 
	utm_medium VARCHAR(255), 
	utm_campaign VARCHAR(255), 
	utm_content VARCHAR(255), 
	utm_term VARCHAR(255), 
	meta JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (kind, code), 
	FOREIGN KEY(channel_id) REFERENCES channels (id)
);

CREATE TABLE content_metric (
	id SERIAL NOT NULL, 
	content_id INTEGER NOT NULL, 
	snapshot_date DATE NOT NULL, 
	views INTEGER, 
	reach INTEGER, 
	likes INTEGER, 
	comments INTEGER, 
	shares INTEGER, 
	saves INTEGER, 
	raw JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (content_id, snapshot_date), 
	FOREIGN KEY(content_id) REFERENCES content (id)
);

CREATE TABLE subscribers (
	id SERIAL NOT NULL, 
	source_system VARCHAR(50) NOT NULL, 
	external_id VARCHAR(255) NOT NULL, 
	channel_id INTEGER, 
	source_id INTEGER, 
	tg_user_id VARCHAR(64), 
	name VARCHAR(255), 
	phone VARCHAR(64), 
	email VARCHAR(255), 
	cuid VARCHAR(128), 
	prodamus_profile_id VARCHAR(128), 
	subscribed BOOLEAN NOT NULL, 
	subscribed_at TIMESTAMP WITH TIME ZONE, 
	last_seen_at TIMESTAMP WITH TIME ZONE, 
	tags JSONB, 
	raw JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (source_system, external_id), 
	FOREIGN KEY(channel_id) REFERENCES channels (id), 
	FOREIGN KEY(source_id) REFERENCES sources (id)
);

CREATE TABLE events (
	id SERIAL NOT NULL, 
	subscriber_id INTEGER, 
	event_type VARCHAR(50) NOT NULL, 
	occurred_at TIMESTAMP WITH TIME ZONE, 
	channel_id INTEGER, 
	source_id INTEGER, 
	content_id INTEGER, 
	funnel_stage_id INTEGER, 
	funnel_step_id INTEGER, 
	tariff_id INTEGER, 
	amount NUMERIC(12, 2), 
	currency VARCHAR(8), 
	source_system VARCHAR(50) NOT NULL, 
	dedup_key VARCHAR(500) NOT NULL, 
	raw JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (source_system, dedup_key), 
	FOREIGN KEY(subscriber_id) REFERENCES subscribers (id), 
	FOREIGN KEY(channel_id) REFERENCES channels (id), 
	FOREIGN KEY(source_id) REFERENCES sources (id), 
	FOREIGN KEY(content_id) REFERENCES content (id), 
	FOREIGN KEY(funnel_stage_id) REFERENCES funnel_stages (id), 
	FOREIGN KEY(funnel_step_id) REFERENCES funnel_steps (id), 
	FOREIGN KEY(tariff_id) REFERENCES tariffs (id)
);

CREATE TABLE payments (
	id SERIAL NOT NULL, 
	subscriber_id INTEGER, 
	tariff_id INTEGER, 
	amount NUMERIC(12, 2), 
	currency VARCHAR(8), 
	status VARCHAR(32) NOT NULL, 
	provider VARCHAR(50) NOT NULL, 
	external_id VARCHAR(255) NOT NULL, 
	paid_at TIMESTAMP WITH TIME ZONE, 
	source_id INTEGER, 
	raw JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (provider, external_id), 
	FOREIGN KEY(subscriber_id) REFERENCES subscribers (id), 
	FOREIGN KEY(tariff_id) REFERENCES tariffs (id), 
	FOREIGN KEY(source_id) REFERENCES sources (id)
);
