#ifndef __FIL_SOCKET_FIL_SOCKET_H__
#define __FIL_SOCKET_FIL_SOCKET_H__

#include "core/filament.h"
#include <sys/socket.h>
#ifdef AF_UNIX
#include <sys/un.h>
#endif

#include <netinet/in.h>

#ifdef HAVE_BLUETOOTH_BLUETOOTH_H
 #include <bluetooth/bluetooth.h>
 #include <bluetooth/l2cap.h>
 #include <bluetooth/hci.h>
 #include <bluetooth/rfcomm.h>
 #include <bluetooth/sco.h>
 #define USE_BLUETOOTH 1
 #if defined(__FreeBSD__)
  #define BTPROTO_L2CAP BLUETOOTH_PROTO_L2CAP
  #define BTPROTO_RFCOMM BLUETOOTH_PROTO_RFCOMM
  #define BTPROTO_HCI BLUETOOTH_PROTO_HCI
  #define SOL_HCI SOL_HCI_RAW
  #define HCI_FILTER SO_HCI_RAW_FILTER
  #define sockaddr_l2 sockaddr_l2cap
  #define sockaddr_rc sockaddr_rfcomm
  #define hci_dev hci_node
  #define _BT_L2_MEMB(sa, memb) ((sa)->l2cap_##memb)
  #define _BT_RC_MEMB(sa, memb) ((sa)->rfcomm_##memb)
  #define _BT_HCI_MEMB(sa, memb) ((sa)->hci_##memb)
 #elif defined(__NetBSD__) || defined(__DragonFly__)
  #define sockaddr_l2 sockaddr_bt
  #define sockaddr_rc sockaddr_bt
  #define sockaddr_hci sockaddr_bt
  #define sockaddr_sco sockaddr_bt
  #define SOL_HCI BTPROTO_HCI
  #define HCI_DATA_DIR SO_HCI_DIRECTION
  #define _BT_L2_MEMB(sa, memb) ((sa)->bt_##memb)
  #define _BT_RC_MEMB(sa, memb) ((sa)->bt_##memb)
  #define _BT_HCI_MEMB(sa, memb) ((sa)->bt_##memb)
  #define _BT_SCO_MEMB(sa, memb) ((sa)->bt_##memb)
 #else
  #define _BT_L2_MEMB(sa, memb) ((sa)->l2_##memb)
  #define _BT_RC_MEMB(sa, memb) ((sa)->rc_##memb)
  #define _BT_HCI_MEMB(sa, memb) ((sa)->hci_##memb)
  #define _BT_SCO_MEMB(sa, memb) ((sa)->sco_##memb)
 #endif
#endif

#ifdef HAVE_BLUETOOTH_H
#include <bluetooth.h>
#endif

#ifdef HAVE_LINUX_NETLINK_H
#include <linux/netlink.h>
#endif

#ifdef HAVE_NETPACKET_PACKET_H
#include <netpacket/packet.h>
#endif

#ifdef HAVE_LINUX_TIPC_H
#include <linux/tipc.h>
#endif

int fil_get_sockaddr_len(int family, int proto);
int fil_socket_init(PyObject *m, PyFilCore_CAPIObject *capi);

#endif /* __FIL_SOCKET_FIL_SOCKET_H__ */
