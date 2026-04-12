ALTER TABLE ONLY public.tbl_attachments
ADD CONSTRAINT tbl_attachments_pkey PRIMARY KEY(attachment_id);

ALTER TABLE ONLY public.tbl_categories_hierarchy
ADD CONSTRAINT tbl_categories_hierarchy_pkey PRIMARY KEY(category_hierarchy_id);

ALTER TABLE ONLY public.tbl_categories
ADD CONSTRAINT tbl_categories_pkey PRIMARY KEY(category_id);

ALTER TABLE ONLY public.tbl_comments
ADD CONSTRAINT tbl_comments_pkey PRIMARY KEY(comment_id);

ALTER TABLE ONLY public.tbl_country
ADD CONSTRAINT tbl_country_pkey PRIMARY KEY(country_name);

ALTER TABLE ONLY public.tbl_followers
ADD CONSTRAINT tbl_followers_pkey PRIMARY KEY(follower_id);

ALTER TABLE ONLY public.tbl_labels
ADD CONSTRAINT tbl_labels_pkey PRIMARY KEY(label_id);

ALTER TABLE ONLY public.tbl_media
ADD CONSTRAINT tbl_media_pkey PRIMARY KEY(media_id);

ALTER TABLE ONLY public.tbl_notifications
ADD CONSTRAINT tbl_notifications_pkey PRIMARY KEY(notification_id);

ALTER TABLE ONLY public.tbl_posts
ADD CONSTRAINT tbl_posts_pkey PRIMARY KEY(post_id);

ALTER TABLE ONLY public.tbl_posts_tags
ADD CONSTRAINT tbl_posts_tags_pkey PRIMARY KEY(post_id, tag_id);

ALTER TABLE ONLY public.tbl_private_messages
ADD CONSTRAINT tbl_private_messages_pkey PRIMARY KEY(message_id);

ALTER TABLE ONLY public.tbl_reactions
ADD CONSTRAINT tbl_reactions_pkey PRIMARY KEY(reaction_id);

ALTER TABLE ONLY public.tbl_reports
ADD CONSTRAINT tbl_reports_pkey PRIMARY KEY(report_id);

ALTER TABLE ONLY public.tbl_status
ADD CONSTRAINT tbl_status_pkey PRIMARY KEY(status_id);

ALTER TABLE ONLY public.tbl_tags_hierarchy
ADD CONSTRAINT tbl_tags_hierarchy_pkey PRIMARY KEY(tag_hierarchy_id);

ALTER TABLE ONLY public.tbl_tags
ADD CONSTRAINT tbl_tags_pkey PRIMARY KEY(tag_id);

ALTER TABLE ONLY public.tbl_user_activity
ADD CONSTRAINT tbl_user_activity_pkey PRIMARY KEY(activity_id);

ALTER TABLE ONLY public.tbl_user_roles
ADD CONSTRAINT tbl_user_roles_pkey PRIMARY KEY(role_id);

ALTER TABLE ONLY public.tbl_users
ADD CONSTRAINT tbl_users_pkey PRIMARY KEY(user_id);

ALTER TABLE ONLY public.tbl_categories_hierarchy
ADD CONSTRAINT fk_category_id
  FOREIGN KEY(category_id) REFERENCES public.tbl_categories(category_id);

ALTER TABLE ONLY public.tbl_categories
ADD CONSTRAINT fk_created_by FOREIGN KEY(created_by) REFERENCES public.tbl_users(user_id);

ALTER TABLE ONLY public.tbl_categories_hierarchy
ADD CONSTRAINT fk_created_by FOREIGN KEY(created_by) REFERENCES public.tbl_users(user_id);

ALTER TABLE ONLY public.tbl_user_roles
ADD CONSTRAINT fk_created_by FOREIGN KEY(created_by) REFERENCES public.tbl_users(user_id);

ALTER TABLE ONLY public.tbl_attachments
ADD CONSTRAINT fk_parent_attachment_id
  FOREIGN KEY(parent_attachment_id) REFERENCES public.tbl_attachments(attachment_id);

ALTER TABLE ONLY public.tbl_attachments
ADD CONSTRAINT fk_post_id FOREIGN KEY(post_id) REFERENCES public.tbl_posts(post_id);

ALTER TABLE ONLY public.tbl_comments
ADD CONSTRAINT fk_post_id FOREIGN KEY(post_id) REFERENCES public.tbl_posts(post_id);

ALTER TABLE ONLY public.tbl_notifications
ADD CONSTRAINT fk_post_id FOREIGN KEY(post_id) REFERENCES public.tbl_posts(post_id);

ALTER TABLE ONLY public.tbl_posts_tags
ADD CONSTRAINT fk_post_id FOREIGN KEY(post_id) REFERENCES public.tbl_posts(post_id);

ALTER TABLE ONLY public.tbl_notifications
ADD CONSTRAINT fk_sender_id FOREIGN KEY(sender_id) REFERENCES public.tbl_users(user_id);

ALTER TABLE ONLY public.tbl_posts_tags
ADD CONSTRAINT fk_tag_id FOREIGN KEY(tag_id) REFERENCES public.tbl_tags(tag_id);

ALTER TABLE ONLY public.tbl_categories
ADD CONSTRAINT fk_updated_by FOREIGN KEY(updated_by) REFERENCES public.tbl_users(user_id);

ALTER TABLE ONLY public.tbl_categories_hierarchy
ADD CONSTRAINT fk_updated_by FOREIGN KEY(updated_by) REFERENCES public.tbl_users(user_id);

ALTER TABLE ONLY public.tbl_user_roles
ADD CONSTRAINT fk_updated_by FOREIGN KEY(updated_by) REFERENCES public.tbl_users(user_id);

ALTER TABLE ONLY public.tbl_attachments
ADD CONSTRAINT fk_user_id FOREIGN KEY(user_id) REFERENCES public.tbl_users(user_id);

ALTER TABLE ONLY public.tbl_comments
ADD CONSTRAINT fk_user_id FOREIGN KEY(user_id) REFERENCES public.tbl_users(user_id);

ALTER TABLE ONLY public.tbl_notifications
ADD CONSTRAINT fk_user_id FOREIGN KEY(user_id) REFERENCES public.tbl_users(user_id);

ALTER TABLE ONLY public.tbl_posts
ADD CONSTRAINT fk_user_id FOREIGN KEY(user_id) REFERENCES public.tbl_users(user_id);

ALTER TABLE ONLY public.tbl_users
ADD CONSTRAINT fk_user_role_id FOREIGN KEY(role_id) REFERENCES public.tbl_user_roles(role_id);
