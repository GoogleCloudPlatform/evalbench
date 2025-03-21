CREATE TABLE tbl_attachments (
  attachment_id INTEGER PRIMARY KEY,
  post_id INTEGER,
  user_id INTEGER,
  file_name TEXT,
  file_path TEXT,
  file_size INTEGER,
  file_type TEXT,
  upload_date DATETIME,
  description TEXT,
  is_active BOOLEAN,
  visibility_status TEXT,
  download_count INTEGER,
  file_extension TEXT,
  uploaded_by_ip TEXT,
  last_modified DATETIME,
  expiration_date DATETIME,
  tags_file TEXT,
  category_file TEXT,
  access_permissions TEXT,
  storage_location TEXT,
  metadata TEXT,
  is_featured BOOLEAN,
  parent_attachment_id INTEGER,
  
  FOREIGN KEY (post_id) REFERENCES tbl_posts(post_id) ON DELETE SET NULL,
  FOREIGN KEY (user_id) REFERENCES tbl_users(user_id) ON DELETE SET NULL,
  FOREIGN KEY (parent_attachment_id) REFERENCES tbl_attachments(attachment_id) ON DELETE SET NULL
);


CREATE TABLE tbl_categories (
  category_id INTEGER PRIMARY KEY,
  created_by INTEGER,
  updated_by INTEGER,
  name TEXT,
  description TEXT,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  parent_category INTEGER,
  slug TEXT,
  image TEXT,
  count INTEGER,
  visibility INTEGER CHECK (visibility IN (0, 1)),
  is_edited INTEGER CHECK (is_edited IN (0, 1)),

  FOREIGN KEY (created_by) REFERENCES tbl_users(user_id) ON DELETE SET NULL,
  FOREIGN KEY (updated_by) REFERENCES tbl_users(user_id) ON DELETE SET NULL
);

CREATE TABLE tbl_categories_hierarchy (
  category_hierarchy_id INTEGER PRIMARY KEY,
  category_id INTEGER,
  created_by INTEGER,
  updated_by INTEGER,
  name TEXT,
  description TEXT,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  status TEXT,
  visibility TEXT,
  orden_priority INTEGER,
  icon TEXT,
  color TEXT,
  metadata TEXT,
  parent_category_name TEXT,

  FOREIGN KEY (category_id) REFERENCES tbl_categories(category_id) ON DELETE SET NULL,
  FOREIGN KEY (created_by) REFERENCES tbl_users(user_id) ON DELETE SET NULL,
  FOREIGN KEY (updated_by) REFERENCES tbl_users(user_id) ON DELETE SET NULL
);

CREATE TABLE tbl_comments (
  comment_id INTEGER PRIMARY KEY,
  content TEXT,
  user_id INTEGER,
  post_id INTEGER,
  parent_comment_id INTEGER,
  likes_count INTEGER DEFAULT 0,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  is_approved INTEGER DEFAULT 0,
  dislikes_count INTEGER DEFAULT 0,
  report_count INTEGER DEFAULT 0,
  is_deleted INTEGER DEFAULT 0, 
  user_ip TEXT,
  user_agent TEXT,
  is_flagged INTEGER DEFAULT 0, 
  reply_count INTEGER DEFAULT 0,
  mentions TEXT,
  attachments TEXT,
  metadata TEXT,
  is_edited INTEGER DEFAULT 0,  

  FOREIGN KEY (user_id) REFERENCES tbl_users(user_id) ON DELETE SET NULL,
  FOREIGN KEY (post_id) REFERENCES tbl_posts(post_id) ON DELETE SET NULL
);

CREATE TABLE tbl_country (
  country_name TEXT PRIMARY KEY,
  country_id INTEGER,
  continent TEXT,
  region TEXT,
  X REAL,
  Y REAL,

  FOREIGN KEY (country_id) REFERENCES tbl_users(user_id) ON DELETE SET NULL
);

CREATE TABLE tbl_followers (
  follower_id INTEGER PRIMARY KEY,
  following_id INTEGER,
  created_at TIMESTAMP,
  updated_at TIMESTAMP ,
  follower_data TEXT DEFAULT NULL
);

CREATE TABLE tbl_labels (
  label_id INTEGER PRIMARY KEY,
  creator_id INTEGER DEFAULT NULL,
  updated_by INTEGER DEFAULT NULL,
  name TEXT DEFAULT NULL,
  description TEXT DEFAULT NULL,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  post_count INTEGER DEFAULT NULL,
  visibility_status TEXT DEFAULT NULL,
  is_active INTEGER DEFAULT NULL,
  usage_frequency INTEGER DEFAULT NULL,
  parent_label_id INTEGER DEFAULT NULL,

  FOREIGN KEY (creator_id) REFERENCES tbl_users(user_id),
  FOREIGN KEY (updated_by) REFERENCES tbl_users(user_id),
  FOREIGN KEY (parent_label_id) REFERENCES tbl_labels(label_id)
);

CREATE TABLE tbl_media (
  media_id INTEGER PRIMARY KEY,
  post_id INTEGER DEFAULT NULL,
  type TEXT DEFAULT NULL,
  url TEXT DEFAULT NULL,
  description TEXT DEFAULT NULL,
  upload_date TEXT DEFAULT NULL,
  updated_at TEXT DEFAULT NULL,
  filename TEXT DEFAULT NULL,
  file_size INTEGER DEFAULT NULL,
  caption TEXT DEFAULT NULL,
  uploader_id INTEGER DEFAULT NULL,
  visibility TEXT DEFAULT NULL,
  access_rights TEXT DEFAULT NULL,
  file_path TEXT DEFAULT NULL,
  metadata TEXT DEFAULT NULL,
  hash TEXT DEFAULT NULL,
  is_encrypted INTEGER DEFAULT NULL,
  encryption_key TEXT DEFAULT NULL,
  encryption_algorithm TEXT DEFAULT NULL,
  status_id INTEGER DEFAULT NULL,

  FOREIGN KEY (post_id) REFERENCES tbl_posts(post_id),
  FOREIGN KEY (uploader_id) REFERENCES tbl_users(user_id),
  FOREIGN KEY (status_id) REFERENCES tbl_status(status_id)
);

CREATE TABLE tbl_notifications (
  notification_id INTEGER PRIMARY KEY,
  user_id INTEGER DEFAULT NULL,
  notification_type TEXT DEFAULT NULL,
  sender_id INTEGER DEFAULT NULL,
  post_id INTEGER DEFAULT NULL,
  comment_id INTEGER DEFAULT NULL,
  notification_message TEXT DEFAULT NULL,
  notification_date TEXT DEFAULT NULL,
  is_read INTEGER DEFAULT NULL,
  is_archived INTEGER DEFAULT NULL,
  is_deleted INTEGER DEFAULT NULL,
  link_to_notification TEXT DEFAULT NULL,
  notification_priority INTEGER DEFAULT NULL,
  additional_data TEXT DEFAULT NULL,
  notification_subject TEXT DEFAULT NULL,
  notification_status TEXT DEFAULT NULL,
  notification_action TEXT DEFAULT NULL,
  related_user_id INTEGER DEFAULT NULL,
  source_application TEXT DEFAULT NULL,
  notification_category TEXT DEFAULT NULL,
  expiration_date TEXT DEFAULT NULL,
  delivery_method TEXT DEFAULT NULL,
  notification_channel TEXT DEFAULT NULL,
  notification_language TEXT DEFAULT NULL,
  recipient_group TEXT DEFAULT NULL,
  notification_tags TEXT DEFAULT NULL,
  notification_expiry_time TEXT DEFAULT NULL,
  notification_context TEXT DEFAULT NULL,
  notification_origin TEXT DEFAULT NULL,

  FOREIGN KEY (user_id) REFERENCES tbl_users(user_id),
  FOREIGN KEY (sender_id) REFERENCES tbl_users(user_id),
  FOREIGN KEY (post_id) REFERENCES tbl_posts(post_id)
);

CREATE TABLE tbl_posts (
  post_id INTEGER PRIMARY KEY,
  user_id INTEGER DEFAULT NULL,
  title TEXT DEFAULT NULL,
  content TEXT DEFAULT NULL,
  published_at TEXT DEFAULT NULL,
  is_published INTEGER DEFAULT NULL,
  views_count INTEGER DEFAULT NULL,
  likes_count INTEGER DEFAULT NULL,
  shares_count INTEGER DEFAULT NULL,
  created_at TEXT DEFAULT NULL,
  updated_at TEXT DEFAULT NULL,
  slug TEXT DEFAULT NULL,
  excerpt TEXT DEFAULT NULL,
  status_id INTEGER DEFAULT NULL,
  category_id INTEGER DEFAULT NULL,
  dislikes_count INTEGER DEFAULT NULL,
  comments_count INTEGER DEFAULT NULL,
  is_featured INTEGER DEFAULT NULL,
  author_name TEXT DEFAULT NULL,
  image_url TEXT DEFAULT NULL,
  tags TEXT DEFAULT NULL,
  reading_time INTEGER DEFAULT NULL,
  external_link TEXT DEFAULT NULL,
  metadata TEXT DEFAULT NULL,

  FOREIGN KEY (user_id) REFERENCES tbl_users(user_id)
);

CREATE TABLE tbl_posts_tags (
  post_id INTEGER NOT NULL,
  tag_id INTEGER NOT NULL,
  comments TEXT DEFAULT NULL,
  label_status TEXT DEFAULT NULL,
  label_at TEXT DEFAULT NULL,

  PRIMARY KEY (post_id, tag_id),
  FOREIGN KEY (tag_id) REFERENCES tbl_tags(tag_id),
  FOREIGN KEY (post_id) REFERENCES tbl_posts(post_id)
);

CREATE TABLE tbl_private_messages (
  message_id INTEGER NOT NULL PRIMARY KEY,
  sender_id INTEGER DEFAULT NULL,
  receiver_id INTEGER DEFAULT NULL,
  subject TEXT DEFAULT NULL,
  body TEXT,
  sending_time TEXT DEFAULT NULL,
  is_read INTEGER DEFAULT NULL,
  is_archived INTEGER DEFAULT NULL,
  is_deleted_by_sender INTEGER DEFAULT NULL,
  is_deleted_by_receiver INTEGER DEFAULT NULL,
  is_flagged INTEGER DEFAULT NULL,
  priority INTEGER DEFAULT NULL,
  attachment_id INTEGER DEFAULT NULL,
  conversation_id INTEGER DEFAULT NULL,
  status TEXT DEFAULT NULL,
  parent_message_id INTEGER DEFAULT NULL,
  read_timestamp TEXT DEFAULT NULL,
  message_data TEXT DEFAULT NULL,

  FOREIGN KEY (sender_id) REFERENCES tbl_users(user_id),
  FOREIGN KEY (receiver_id) REFERENCES tbl_users(user_id),
  FOREIGN KEY (attachment_id) REFERENCES tbl_attachments(attachment_id),
  FOREIGN KEY (parent_message_id) REFERENCES tbl_private_messages(message_id)
);

CREATE TABLE tbl_reactions (
  reaction_id INTEGER NOT NULL PRIMARY KEY,
  comment_id INTEGER DEFAULT NULL,
  user_id INTEGER DEFAULT NULL,
  post_id INTEGER DEFAULT NULL,
  reaction_type TEXT DEFAULT NULL,
  reaction_date TEXT DEFAULT NULL,
  reaction_score INTEGER DEFAULT NULL,
  reaction_text TEXT DEFAULT NULL,
  is_active INTEGER DEFAULT NULL,
  reaction_count INTEGER DEFAULT NULL,
  source_ip TEXT DEFAULT NULL,
  device_info TEXT DEFAULT NULL,

  FOREIGN KEY (comment_id) REFERENCES tbl_comments(comment_id)
);

CREATE TABLE tbl_reports (
  report_id INTEGER NOT NULL PRIMARY KEY,
  user_id INTEGER DEFAULT NULL,
  post_id INTEGER DEFAULT NULL,
  report_reason TEXT DEFAULT NULL,
  report_timestamp TEXT DEFAULT NULL,
  is_resolved INTEGER DEFAULT NULL,
  resolved_by INTEGER DEFAULT NULL,
  resolution_timestamp TEXT DEFAULT NULL,
  report_comments TEXT DEFAULT NULL,
  severity_level INTEGER DEFAULT NULL,
  report_type TEXT DEFAULT NULL,
  report_entity_type TEXT DEFAULT NULL,
  report_entity_id INTEGER DEFAULT NULL,
  action_taken TEXT DEFAULT NULL,
  action_timestamp TEXT DEFAULT NULL,
  report_ip_address TEXT DEFAULT NULL,
  reported_user_id INTEGER DEFAULT NULL,
  reported_comment_id INTEGER DEFAULT NULL,
  reported_user_reputation INTEGER DEFAULT NULL,
  report_category TEXT DEFAULT NULL,
  reported_context_text TEXT DEFAULT NULL,
  attachments TEXT DEFAULT NULL,
  flagged_user INTEGER DEFAULT NULL,
  report_source TEXT DEFAULT NULL,
  report_feedback TEXT DEFAULT NULL,
  report_verification_status TEXT DEFAULT NULL,
  report_mechanism TEXT DEFAULT NULL,
  report_priority INTEGER DEFAULT NULL,
  assigned_to INTEGER DEFAULT NULL,
  report_history TEXT DEFAULT NULL,

  FOREIGN KEY (user_id) REFERENCES tbl_users(user_id),
  FOREIGN KEY (post_id) REFERENCES tbl_posts(post_id),
  FOREIGN KEY (resolved_by) REFERENCES tbl_users(user_id),
  FOREIGN KEY (reported_user_id) REFERENCES tbl_users(user_id),
  FOREIGN KEY (reported_comment_id) REFERENCES tbl_comments(comment_id)
);

CREATE TABLE tbl_status (
  status_id INTEGER NOT NULL PRIMARY KEY,
  Name TEXT DEFAULT NULL
);

CREATE TABLE tbl_tags (
  tag_id INTEGER NOT NULL PRIMARY KEY,
  creator_id INTEGER DEFAULT NULL,
  category_id INTEGER DEFAULT NULL,
  name TEXT DEFAULT NULL,
  description TEXT DEFAULT NULL,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  slug TEXT DEFAULT NULL,
  count INTEGER DEFAULT NULL,
  visibility BOOLEAN DEFAULT NULL,
  active BOOLEAN DEFAULT NULL,
  parent_tag INTEGER DEFAULT NULL,
  status_id INTEGER DEFAULT NULL,
  access_level INTEGER DEFAULT NULL,
  is_approved BOOLEAN DEFAULT NULL,
  metadata TEXT DEFAULT NULL,
  is_edited BOOLEAN DEFAULT NULL,
  FOREIGN KEY (creator_id) REFERENCES tbl_users(user_id),
  FOREIGN KEY (category_id) REFERENCES tbl_categories(category_id)
);

CREATE TABLE tbl_tags_hierarchy (
  tag_hierarchy_id INTEGER NOT NULL PRIMARY KEY,
  tag_id INTEGER DEFAULT NULL,
  relationship_type TEXT DEFAULT NULL,
  weight DECIMAL(10,2) DEFAULT NULL,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  active BOOLEAN DEFAULT NULL,
  description TEXT DEFAULT NULL,
  creator_id INTEGER DEFAULT NULL,
  modifier_id INTEGER DEFAULT NULL,
  is_deleted BOOLEAN DEFAULT NULL,
  deleted_at TIMESTAMP DEFAULT NULL,
  revision_history TEXT DEFAULT NULL,
  comments TEXT DEFAULT NULL
);

CREATE TABLE tbl_user_activity (
  activity_id INTEGER PRIMARY KEY,
  user_id INTEGER,
  activity_type TEXT,
  activity_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  post_id INTEGER,
  comment_id INTEGER,
  activity_description TEXT,
  activity_source TEXT,
  activity_location TEXT,
  activity_duration INTEGER,
  activity_device TEXT,
  activity_status TEXT,
  activity_impact TEXT,
  activity_privacy_level TEXT,
  activity_related_users TEXT,
  activity_extra_data TEXT,
  ip_address TEXT,
  user_agent TEXT,
  url TEXT,
  session_id TEXT,
  browser TEXT,
  operating_system TEXT,
  referrer TEXT,
  event_id INTEGER,
  error_message TEXT,
  location_coordinates TEXT,
  session_duration INTEGER,
  FOREIGN KEY (user_id) REFERENCES tbl_users(user_id) ON DELETE CASCADE,
  FOREIGN KEY (post_id) REFERENCES tbl_posts(post_id) ON DELETE CASCADE,
  FOREIGN KEY (comment_id) REFERENCES tbl_comments(comment_id) ON DELETE CASCADE
);

CREATE TABLE tbl_user_roles (
  role_id INTEGER PRIMARY KEY,
  created_by INTEGER,
  updated_by INTEGER,
  description TEXT,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  permissions TEXT,
  role_name TEXT,
  is_active INTEGER,
  restrictions TEXT,
  expiry_date DATE,
  created_by_ip TEXT,
  updated_by_ip TEXT,
  created_by_device TEXT,
  updated_by_device TEXT,
  active_sessions INTEGER,
  last_sessions TEXT,
  FOREIGN KEY (created_by) REFERENCES tbl_users(user_id) ON DELETE SET NULL,
  FOREIGN KEY (updated_by) REFERENCES tbl_users(user_id) ON DELETE SET NULL
);

CREATE TABLE tbl_users (
  user_id INTEGER PRIMARY KEY,
  first_name TEXT,
  last_name TEXT,
  username TEXT,
  password_hash TEXT,
  social_media_links TEXT,
  contact_info TEXT,
  avatar_url TEXT,
  bio TEXT,
  website TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  notification_preferences TEXT,
  role_id INTEGER,
  last_login TIMESTAMP,
  active INTEGER
);