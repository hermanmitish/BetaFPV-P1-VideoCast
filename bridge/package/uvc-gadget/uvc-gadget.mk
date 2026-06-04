################################################################################
# uvc-gadget — userspace UVC gadget feeder (wlhe fork)
################################################################################

UVC_GADGET_VERSION = 74522b25f982204c244357ca982a281d68352976
UVC_GADGET_SITE = $(call github,wlhe,uvc-gadget,$(UVC_GADGET_VERSION))
UVC_GADGET_LICENSE = GPL-2.0

# Simple Makefile-based build. Force CC to the Buildroot cross toolchain (a command-line CC overrides
# the Makefile's CC=$(CROSS_COMPILE)gcc), and pass CFLAGS so the target sysroot headers are used.
define UVC_GADGET_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) CC="$(TARGET_CC)" CFLAGS="$(TARGET_CFLAGS)" -C $(@D)
endef

define UVC_GADGET_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/uvc-gadget $(TARGET_DIR)/usr/bin/uvc-gadget
endef

$(eval $(generic-package))
