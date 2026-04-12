CREATE TABLE public.tbl_attachments(
  attachment_id integer NOT NULL,
  post_id integer,
  user_id integer,
  file_name character varying(255),
  file_path character varying(255),
  file_size integer,
  file_type character varying(50),
  upload_date timestamp without time zone,
  description text,
  is_active boolean,
  visibility_status character varying(20),
  download_count integer,
  file_extension character varying(10),
  uploaded_by_ip character varying(15),
  last_modified timestamp without time zone,
  expiration_date timestamp without time zone,
  tags_file jsonb,
  category_file jsonb,
  access_permissions jsonb,
  storage_location character varying(255),
  metadata jsonb,
  is_featured boolean,
  parent_attachment_id integer
);
CREATE SEQUENCE public.tbl_attachments_attachment_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.tbl_attachments_attachment_id_seq OWNED BY public.tbl_attachments.attachment_id;
ALTER TABLE ONLY public.tbl_attachments
ALTER COLUMN attachment_id
SET DEFAULT nextval(
    'public.tbl_attachments_attachment_id_seq'::regclass
  );
CREATE TABLE public.tbl_categories(
  category_id integer NOT NULL,
  created_by integer,
  updated_by integer,
  name character varying(255),
  description text,
  created_at timestamp without time zone,
  updated_at timestamp without time zone,
  parent_category integer,
  slug character varying(255),
  image character varying(255),
  count integer,
  visibility boolean,
  is_edited boolean
);
CREATE SEQUENCE public.tbl_categories_category_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.tbl_categories_category_id_seq OWNED BY public.tbl_categories.category_id;
CREATE TABLE public.tbl_categories_hierarchy(
  category_hierarchy_id integer NOT NULL,
  category_id integer,
  created_by integer,
  updated_by integer,
  name character varying(255),
  description text,
  created_at timestamp without time zone,
  updated_at timestamp without time zone,
  status character varying(50),
  visibility character varying(50),
  orden_priority integer,
  icon character varying(255),
  color character varying(50),
  metadata text,
  parent_category_name character varying(255)
);
CREATE SEQUENCE public.tbl_categories_hierarchy_category_hierarchy_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.tbl_categories_hierarchy_category_hierarchy_id_seq OWNED BY public.tbl_categories_hierarchy.category_hierarchy_id;
CREATE TABLE public.tbl_comments(
  comment_id integer NOT NULL,
  content text,
  user_id integer,
  post_id integer,
  parent_comment_id integer,
  likes_count integer,
  created_at timestamp without time zone,
  updated_at timestamp without time zone,
  is_approved boolean,
  dislikes_count integer,
  report_count integer,
  is_deleted boolean,
  user_ip character varying(255),
  user_agent character varying(255),
  is_flagged boolean,
  reply_count integer,
  mentions jsonb,
  attachments jsonb,
  metadata jsonb,
  is_edited boolean
);
CREATE SEQUENCE public.tbl_comments_comment_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.tbl_comments_comment_id_seq OWNED BY public.tbl_comments.comment_id;
CREATE TABLE public.tbl_country(
  country_name character varying(255) NOT NULL,
  country_id integer,
  continent character varying(255),
  region character varying(255),
  x double precision,
  y double precision
);
CREATE TABLE public.tbl_followers(
  follower_id integer NOT NULL,
  following_id integer,
  created_at timestamp without time zone,
  updated_at timestamp without time zone,
  follower_data jsonb
);
CREATE SEQUENCE public.tbl_followers_follower_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.tbl_followers_follower_id_seq OWNED BY public.tbl_followers.follower_id;
CREATE TABLE public.tbl_labels(
  label_id integer NOT NULL,
  creator_id integer,
  updated_by integer,
  name character varying(255),
  description text,
  created_at timestamp without time zone,
  updated_at timestamp without time zone,
  post_count integer,
  visibility_status character varying(50),
  is_active boolean,
  usage_frequency integer,
  parent_label_id integer
);
CREATE SEQUENCE public.tbl_labels_label_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.tbl_labels_label_id_seq OWNED BY public.tbl_labels.label_id;
CREATE TABLE public.tbl_media(
  media_id integer NOT NULL,
  post_id integer,
  type character varying(255),
  url character varying(255),
  description text,
  upload_date timestamp without time zone,
  updated_at timestamp without time zone,
  filename character varying(255),
  file_size integer,
  caption text,
  uploader_id integer,
  visibility character varying(50),
  access_rights json,
  file_path character varying(255),
  metadata json,
  HASH character varying(255),
  is_encrypted boolean,
  encryption_key character varying(255),
  encryption_algorithm character varying(50),
  status_id integer
);
CREATE SEQUENCE public.tbl_media_media_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.tbl_media_media_id_seq OWNED BY public.tbl_media.media_id;
CREATE TABLE public.tbl_notifications(
  notification_id integer NOT NULL,
  user_id integer,
  notification_type character varying(255),
  sender_id integer,
  post_id integer,
  comment_id integer,
  notification_message text,
  notification_date timestamp without time zone,
  is_read boolean,
  is_archived boolean,
  is_deleted boolean,
  link_to_notification character varying(255),
  notification_priority integer,
  additional_data json,
  notification_subject character varying(255),
  notification_status character varying(50),
  notification_action character varying(50),
  related_user_id integer,
  source_application character varying(255),
  notification_category character varying(255),
  expiration_date timestamp without time zone,
  delivery_method character varying(50),
  notification_channel character varying(50),
  notification_language character varying(50),
  recipient_group character varying(255),
  notification_tags json,
  notification_expiry_time timestamp without time zone,
  notification_context json,
  notification_origin character varying(255)
);
CREATE SEQUENCE public.tbl_notifications_notification_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.tbl_notifications_notification_id_seq OWNED BY public.tbl_notifications.notification_id;
CREATE TABLE public.tbl_posts(
  post_id integer NOT NULL,
  user_id integer,
  title character varying(255),
  content text,
  published_at timestamp without time zone,
  is_published boolean,
  views_count integer,
  likes_count integer,
  shares_count integer,
  created_at timestamp without time zone,
  updated_at timestamp without time zone,
  slug character varying(255),
  excerpt text,
  status_id integer,
  category_id integer,
  dislikes_count integer,
  comments_count integer,
  is_featured boolean,
  author_name character varying(255),
  image_url character varying(255),
  tags json,
  reading_time integer,
  external_link character varying(255),
  metadata json
);
CREATE SEQUENCE public.tbl_posts_post_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.tbl_posts_post_id_seq OWNED BY public.tbl_posts.post_id;
CREATE TABLE public.tbl_posts_tags(
  post_id integer NOT NULL,
  tag_id integer NOT NULL,
  comments json,
  label_status character varying(50),
  label_at timestamp without time zone
);
CREATE TABLE public.tbl_private_messages(
  message_id integer NOT NULL,
  sender_id integer,
  receiver_id integer,
  subject character varying(255),
  body text,
  sending_time timestamp without time zone,
  is_read boolean,
  is_archived boolean,
  is_deleted_by_sender boolean,
  is_deleted_by_receiver boolean,
  is_flagged boolean,
  priority integer,
  attachment_id integer,
  conversation_id integer,
  status character varying(50),
  parent_message_id integer,
  read_timestamp timestamp without time zone,
  message_data json
);
CREATE SEQUENCE public.tbl_private_messages_message_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.tbl_private_messages_message_id_seq OWNED BY public.tbl_private_messages.message_id;
CREATE TABLE public.tbl_reactions(
  reaction_id integer NOT NULL,
  comment_id integer,
  user_id integer,
  post_id integer,
  reaction_type character varying(255),
  reaction_date timestamp without time zone,
  reaction_score integer,
  reaction_text text,
  is_active boolean,
  reaction_count integer,
  source_ip character varying(255),
  device_info json
);
CREATE SEQUENCE public.tbl_reactions_reaction_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.tbl_reactions_reaction_id_seq OWNED BY public.tbl_reactions.reaction_id;
CREATE TABLE public.tbl_reports(
  report_id integer NOT NULL,
  user_id integer,
  post_id integer,
  report_reason character varying(255),
  report_timestamp timestamp without time zone,
  is_resolved boolean,
  resolved_by integer,
  resolution_timestamp timestamp without time zone,
  report_comments text,
  severity_level integer,
  report_type character varying(50),
  report_entity_type character varying(50),
  report_entity_id integer,
  action_taken text,
  action_timestamp timestamp without time zone,
  report_ip_address character varying(15),
  reported_user_id integer,
  reported_comment_id integer,
  reported_user_reputation integer,
  report_category character varying(50),
  reported_context_text text,
  attachments json,
  flagged_user boolean,
  report_source character varying(50),
  report_feedback text,
  report_verification_status character varying(50),
  report_mechanism character varying(50),
  report_priority integer,
  assigned_to integer,
  report_history json
);
CREATE SEQUENCE public.tbl_reports_report_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.tbl_reports_report_id_seq OWNED BY public.tbl_reports.report_id;
CREATE TABLE public.tbl_status(
  status_id integer NOT NULL,
  name character varying(255)
);
CREATE SEQUENCE public.tbl_status_status_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.tbl_status_status_id_seq OWNED BY public.tbl_status.status_id;
CREATE TABLE public.tbl_tags(
  tag_id integer NOT NULL,
  creator_id integer,
  category_id integer,
  name character varying(255),
  description text,
  created_at timestamp without time zone,
  updated_at timestamp without time zone,
  slug character varying(255),
  count integer,
  visibility boolean,
  active boolean,
  parent_tag integer,
  status_id integer,
  access_level integer,
  is_approved boolean,
  metadata json,
  is_edited boolean
);
CREATE TABLE public.tbl_tags_hierarchy(
  tag_hierarchy_id integer NOT NULL,
  tag_id integer,
  relationship_type character varying(255),
  weight numeric(10, 2),
  created_at timestamp without time zone,
  updated_at timestamp without time zone,
  active boolean,
  description text,
  creator_id integer,
  modifier_id integer,
  is_deleted boolean,
  deleted_at timestamp without time zone,
  revision_history json,
  comments json
);
CREATE SEQUENCE public.tbl_tags_hierarchy_tag_hierarchy_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.tbl_tags_hierarchy_tag_hierarchy_id_seq OWNED BY public.tbl_tags_hierarchy.tag_hierarchy_id;
CREATE SEQUENCE public.tbl_tags_tag_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.tbl_tags_tag_id_seq OWNED BY public.tbl_tags.tag_id;
CREATE TABLE public.tbl_user_activity(
  activity_id integer NOT NULL,
  user_id integer,
  activity_type character varying(255),
  activity_timestamp timestamp without time zone,
  post_id integer,
  comment_id integer,
  activity_description text,
  activity_source character varying(255),
  activity_location character varying(255),
  activity_duration integer,
  activity_device character varying(255),
  activity_status character varying(50),
  activity_impact character varying(50),
  activity_privacy_level character varying(50),
  activity_related_users json,
  activity_extra_data json,
  ip_address character varying(15),
  user_agent character varying(255),
  url character varying(255),
  session_id character varying(255),
  browser character varying(255),
  operating_system character varying(255),
  referrer character varying(255),
  event_id integer,
  error_message text,
  location_coordinates json,
  session_duration integer
);
CREATE SEQUENCE public.tbl_user_activity_activity_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.tbl_user_activity_activity_id_seq OWNED BY public.tbl_user_activity.activity_id;
CREATE TABLE public.tbl_user_roles(
  role_id integer NOT NULL,
  created_by integer,
  updated_by integer,
  description character varying(255),
  created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
  updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
  permissions json,
  role_name character varying(50),
  is_active boolean,
  restrictions json,
  expiry_date date,
  created_by_ip character varying(50),
  updated_by_ip character varying(50),
  created_by_device character varying(100),
  updated_by_device character varying(100),
  active_sessions integer,
  last_sessions json
);
CREATE SEQUENCE public.tbl_user_roles_role_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.tbl_user_roles_role_id_seq OWNED BY public.tbl_user_roles.role_id;
CREATE TABLE public.tbl_users(
  user_id integer NOT NULL,
  first_name character varying(255) DEFAULT NULL::character varying,
  last_name character varying(255) DEFAULT NULL::character varying,
  username character varying(255) DEFAULT NULL::character varying,
  password_hash character varying(255) DEFAULT NULL::character varying,
  social_media_links json,
  contact_info json,
  avatar_url character varying(255) DEFAULT NULL::character varying,
  bio text,
  website character varying(255) DEFAULT NULL::character varying,
  created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
  updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
  notification_preferences character varying(255) DEFAULT NULL::character varying,
  role_id integer,
  last_login timestamp without time zone,
  active character varying(1) DEFAULT NULL::character varying
);
ALTER TABLE ONLY public.tbl_categories
ALTER COLUMN category_id
SET DEFAULT nextval(
    'public.tbl_categories_category_id_seq'::regclass
  );
ALTER TABLE ONLY public.tbl_categories_hierarchy
ALTER COLUMN category_hierarchy_id
SET DEFAULT nextval(
    'public.tbl_categories_hierarchy_category_hierarchy_id_seq'::regclass
  );
ALTER TABLE ONLY public.tbl_comments
ALTER COLUMN comment_id
SET DEFAULT nextval('public.tbl_comments_comment_id_seq'::regclass);
ALTER TABLE ONLY public.tbl_followers
ALTER COLUMN follower_id
SET DEFAULT nextval('public.tbl_followers_follower_id_seq'::regclass);
ALTER TABLE ONLY public.tbl_labels
ALTER COLUMN label_id
SET DEFAULT nextval('public.tbl_labels_label_id_seq'::regclass);
ALTER TABLE ONLY public.tbl_media
ALTER COLUMN media_id
SET DEFAULT nextval('public.tbl_media_media_id_seq'::regclass);
ALTER TABLE ONLY public.tbl_notifications
ALTER COLUMN notification_id
SET DEFAULT nextval(
    'public.tbl_notifications_notification_id_seq'::regclass
  );
ALTER TABLE ONLY public.tbl_posts
ALTER COLUMN post_id
SET DEFAULT nextval('public.tbl_posts_post_id_seq'::regclass);
ALTER TABLE ONLY public.tbl_private_messages
ALTER COLUMN message_id
SET DEFAULT nextval(
    'public.tbl_private_messages_message_id_seq'::regclass
  );
ALTER TABLE ONLY public.tbl_reactions
ALTER COLUMN reaction_id
SET DEFAULT nextval('public.tbl_reactions_reaction_id_seq'::regclass);
ALTER TABLE ONLY public.tbl_reports
ALTER COLUMN report_id
SET DEFAULT nextval('public.tbl_reports_report_id_seq'::regclass);
ALTER TABLE ONLY public.tbl_status
ALTER COLUMN status_id
SET DEFAULT nextval('public.tbl_status_status_id_seq'::regclass);
ALTER TABLE ONLY public.tbl_tags
ALTER COLUMN tag_id
SET DEFAULT nextval('public.tbl_tags_tag_id_seq'::regclass);
ALTER TABLE ONLY public.tbl_tags_hierarchy
ALTER COLUMN tag_hierarchy_id
SET DEFAULT nextval(
    'public.tbl_tags_hierarchy_tag_hierarchy_id_seq'::regclass
  );
ALTER TABLE ONLY public.tbl_user_activity
ALTER COLUMN activity_id
SET DEFAULT nextval(
    'public.tbl_user_activity_activity_id_seq'::regclass
  );
ALTER TABLE ONLY public.tbl_user_roles
ALTER COLUMN role_id
SET DEFAULT nextval('public.tbl_user_roles_role_id_seq'::regclass);