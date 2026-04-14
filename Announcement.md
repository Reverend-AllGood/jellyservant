JellyServant: A Self-Hosted Bridge for Jellyfin 🚀

<img width="1541" height="1306" alt="image" src="https://github.com/user-attachments/assets/f214e65d-cb59-4dcf-bf2b-719c3643247e" />

Hey everyone!

I wanted to share a project I’ve been working on called JellyServant. It’s a self-hosted utility designed to bridge two Jellyfin instances without the need for massive file transfers or hosting a third middle-man server.


The Concept

I wanted a way to "borrow" media from another server—to see the metadata as if I were logged into that server directly, stream the media, and interact without the complexity of a third instance (like in Jellyswarm).

The idea of sharing one giant server didn't sit well with my group; we much prefer our own individuality. This allows us to maintain our own setups while still accessing each other's content seamlessly.
How it Works

JellyServant scans a source media library and generates:

    Local .strm files: Pointers that tell the remote Jellyfin where to stream the file from.

    Local .nfo files: These ensure metadata, images, and sorting stay 100% consistent with the source.

It then serves those files to a library you create on your local instance as if they were native files.

    Note on the name: The name and images take inspiration from the Yu-Gi-Oh! card Skull Servant. Yes, I do play; no, I’m not sorry; and yes, Magic is better.

Current Features

    Web UI: Evolved from a simple Python script into a fully containerized service.

    Nginx Integration: Built to work behind Nginx for secure, encrypted streaming between nodes.

    Storage Optimization: Specifically tailored to output media directly into designated storage directories.

    Zero Storage Overhead: Mirror a 50TB library using only a few gigabytes of metadata and pointer files.

Current State & Goals

<img width="1542" height="1294" alt="image" src="https://github.com/user-attachments/assets/302bd484-7f94-4e6c-9551-cedad5232ec7" />


JellyServant is currently in v1.4 Beta.

    The Progress: To put it bluntly, I am about 3/4 of the way done.

    Testing: Finalizing the UI and integrating an APScheduler into the Docker container to allow for automated weekly scans.

    Delta Sync: I'm working on a way for the app to remember your selections so it only generates files for new content, saving on processing and API calls.

    The Timeline: My ultimate goal is to have the stable 1.0 version fully fleshed out and working by the end of July 2026. I’m trying to limit myself to a max of 2.0 for the beta phase to ensure I don't get stuck in "feature creep" forever.

The End Game

The goal is a "set it and forget it" bridge. Connect to a remote server, see the media instantly, and stay up to date without babysitting scripts or fixing broken metadata.

An added bonus: this method still utilizes the host's hardware for streaming. This is perfect for a "set-top box" scenario where the local device might not be powerful enough to transcode, but the source server is.



<img width="1543" height="1305" alt="image" src="https://github.com/user-attachments/assets/5d02a009-95b4-4761-8185-fd4f9251ed87" />
