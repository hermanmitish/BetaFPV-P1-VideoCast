################################################################################
# artlynk-view — the bridge viewer service (a shell script + GStreamer)
################################################################################

ARTLYNK_VIEW_VERSION = 1.0
ARTLYNK_VIEW_SITE = $(BR2_EXTERNAL_ARTLYNK_BRIDGE_PATH)/package/artlynk-view/src
ARTLYNK_VIEW_SITE_METHOD = local
ARTLYNK_VIEW_LICENSE = MIT

# Runtime needs GStreamer + the plugins the pipeline uses.
ARTLYNK_VIEW_DEPENDENCIES = gstreamer1 gst1-plugins-base gst1-plugins-good gst1-plugins-bad

define ARTLYNK_VIEW_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/artlynk-view $(TARGET_DIR)/usr/bin/artlynk-view
endef

$(eval $(generic-package))
