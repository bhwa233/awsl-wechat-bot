CREATE TABLE archive_groups (
    account_id TEXT NOT NULL,
    talker TEXT NOT NULL,
    display_name TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (account_id, talker)
);

CREATE TABLE archive_messages (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    talker TEXT NOT NULL,
    server_id TEXT NOT NULL,
    create_time INTEGER NOT NULL,
    sender TEXT NOT NULL,
    sender_name TEXT NOT NULL,
    msg_type INTEGER NOT NULL,
    content TEXT NOT NULL,
    text TEXT NOT NULL,
    is_self INTEGER NOT NULL,
    at_users TEXT NOT NULL,
    observed_at INTEGER NOT NULL
);
CREATE INDEX archive_messages_time ON archive_messages(account_id, talker, create_time, id);
CREATE INDEX archive_messages_sender_time ON archive_messages(account_id, talker, sender, create_time, id);

-- 一个云端消息可以来自多台设备；无 server_id 的观察也能在稍后关联到正式消息。
CREATE TABLE archive_observations (
    account_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    talker TEXT NOT NULL,
    local_key TEXT NOT NULL,
    message_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    local_id INTEGER NOT NULL,
    observed_at INTEGER NOT NULL,
    PRIMARY KEY (account_id, source_id, talker, local_key)
);
CREATE INDEX archive_observations_message ON archive_observations(message_id);
