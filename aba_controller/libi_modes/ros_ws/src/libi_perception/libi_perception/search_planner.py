def search_command(elapsed, cfg, lkd=1.0):
    """3-phase open-loop recovery given elapsed seconds since search start.

    Timeline:
      [0, HOLD)                      -> hold (0)
      [HOLD, HOLD+SCAN)              -> scan sweep (ANGULAR_Z_SEARCH * lkd)
      [.., + TURN)                   -> ~180 deg turn (ANGULAR_Z_SEARCH)
      [.., + SCAN)                   -> scan sweep (ANGULAR_Z_SEARCH * -lkd)
      after                          -> done (0)
    """
    hold = cfg.SEARCH_HOLD_SEC
    scan = cfg.SEARCH_SCAN_SEC
    turn = cfg.SEARCH_TURN_ANGLE / cfg.ANGULAR_Z_SEARCH
    t_scan1_end = hold + scan
    t_turn_end = t_scan1_end + turn
    t_scan2_end = t_turn_end + scan

    if elapsed < hold:
        return 0.0, False
    if elapsed < t_scan1_end:
        return cfg.ANGULAR_Z_SEARCH * lkd, False
    if elapsed < t_turn_end:
        return cfg.ANGULAR_Z_SEARCH, False
    if elapsed < t_scan2_end:
        return cfg.ANGULAR_Z_SEARCH * -lkd, False
    return 0.0, True
