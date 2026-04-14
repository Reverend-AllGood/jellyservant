JellyServant: A Jellyfin-to-Jellyfin Media Bridge
Overview

JellyServant is a self-hosted utility designed to bridge two Jellyfin instances. It allows a remote "mirror" server to display and play a source library without the need to transfer or store massive media files locally.
How It Works

The service operates on a "pointer" philosophy:

    Scanning: JellyServant scans the source media library.

    Pointer Generation: It automatically generates .strm (stream) and .nfo (metadata) files.

    Streaming: When a user hits "Play" on the mirror server, the .strm file directs the traffic back to the source server.

    Integration: Built to run as a containerized service with a Web UI, it integrates seamlessly with Nginx for secure, remote streaming.

Development Philosophy

    "I view this project as a way to 'borrow' media—creating a seamless, mirrored experience where the remote library looks and functions exactly like the original, without the storage overhead."

Technical Features

    Containerized: Easily deployed via Docker.

    Web UI: Moved beyond a simple Python script to a manageable interface.

    Storage Pathing: Specifically tailored to output media directly into designated storage directories (optimized for setups like Sigilnode).
