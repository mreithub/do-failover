`do-failover` - automatic failover for HTTP(S) based services on DigitalOcean
=============

`do-failover` helps you switch between a main and hot-standby server on [DigitalOcean][do] (using their Floating IP feature).

## Environment

- `API_KEY`: Your digitalocean.com API key
- `FAILOVER_MODE`: 'main' or 'standby' (or disabled if not set)
- `FLOATING_IP`: the IP that's been shared by the main and standby server
- `FAILOVER_CHECK`: local URL(s) to check to determine the health of *this* server (split by '|')
- `FAILOVER_MAIN`: URL(s) of the main server (if we're on the standby server) (URLs are split by '|')
- `FAILOVER_MAIN_HOST` (optional): Set the 'Host' header for requests to `FAILOVER_MAIN` URLs (allows to ignore DNS while still checking the validity of SSL certs)

## Operation

There are two modes: `main` and `standby`:

### `main`:

When in main mode, the failover script will try to acquire/keep the Floating IP (as long as all of the URLs in `FAILOVER_CHECK` can be successfully fetched).

It runs as follows (once a minute):

- Request all the URLs in `FAILOVER_CHECK`. If one or more of the checks don't succeed -> fail
- Check if we have the floating IP in question. If we do -> success
- Try to acquire the floating IP.


### `standby`:

When in standby mode, the script will only take control of the floating IP if the main server is deemed unhealthy (i.e. at least one of the URLs in `FAILOVER_MAIN` return with an error code)

It does the following (once a minute):

- Checks all the URLs in `FAILOVER_CHECK` (i.e. the health of the local service(s)). If one or more of the checks didn't succeed -> fail
- Checks if it controls the floating IP. If it does -> success
- Check the URLs in `FAILOVER_MAIN` (to see if the main server is healthy). If it is -> success
- Try to acquire the floating IP.



