//! MQTT publishing with broker acknowledgments and background keepalive.

use crate::{config::Config, sys::Result};
use rumqttc::{AsyncClient, Event, EventLoop, Incoming, LastWill, MqttOptions, Outgoing, QoS};
use std::time::Duration;
use tokio::{sync::mpsc, task::JoinHandle};

const OPERATION_TIMEOUT: Duration = Duration::from_secs(3);

enum Delivery {
    Sent(u16),
    Acknowledged(u16),
}

pub struct Mqtt {
    client: AsyncClient,
    deliveries: mpsc::Receiver<Delivery>,
    worker: JoinHandle<()>,
}

impl Mqtt {
    pub async fn connect(config: &Config, doorbell: bool) -> Result<Self> {
        let client_id = if doorbell {
            format!("{}-doorbell", config.client)
        } else {
            config.client.clone()
        };
        let availability = if doorbell {
            "doorbell/availability"
        } else {
            "availability"
        };
        let mut options = MqttOptions::new(client_id, &config.host, config.port);
        options.set_clean_session(true);
        options.set_keep_alive(Duration::from_secs(10));
        options.set_inflight(1);
        options.set_max_packet_size(4096, 65536);
        options.set_last_will(LastWill::new(
            format!("{}/{availability}", config.prefix),
            "offline",
            QoS::AtLeastOnce,
            true,
        ));
        if !config.user.is_empty() {
            options.set_credentials(&config.user, &config.pass);
        }
        let (client, mut event_loop) = AsyncClient::new(options, 8);
        tokio::time::timeout(OPERATION_TIMEOUT, async {
            loop {
                if matches!(
                    event_loop.poll().await.map_err(|_| ())?,
                    Event::Incoming(Incoming::ConnAck(_))
                ) {
                    return Ok(());
                }
            }
        })
        .await
        .map_err(|_| ())??;

        let (sender, deliveries) = mpsc::channel(8);
        let worker = tokio::task::spawn_local(drive(event_loop, sender));
        Ok(Self {
            client,
            deliveries,
            worker,
        })
    }

    /// Return only after PUBACK, not merely after adding a request to the queue.
    /// The bridge retains a pending ring until this succeeds.
    pub async fn publish(
        &mut self,
        config: &Config,
        suffix: &[u8],
        payload: &[u8],
        retained: bool,
    ) -> Result<()> {
        let suffix = std::str::from_utf8(suffix).map_err(|_| ())?;
        let topic = format!("{}/{suffix}", config.prefix);
        tokio::time::timeout(OPERATION_TIMEOUT, async {
            self.check_connection()?;
            self.client
                .publish(topic, QoS::AtLeastOnce, retained, payload)
                .await
                .map_err(|_| ())?;
            let mut sent_id = None;
            while let Some(delivery) = self.deliveries.recv().await {
                match delivery {
                    Delivery::Sent(id) => sent_id = Some(id),
                    Delivery::Acknowledged(id) if sent_id == Some(id) => return Ok(()),
                    Delivery::Acknowledged(_) => return Err(()),
                }
            }
            Err(())
        })
        .await
        .map_err(|_| ())?
    }

    pub fn check_connection(&self) -> Result<()> {
        if self.worker.is_finished() {
            Err(())
        } else {
            Ok(())
        }
    }
}

impl Drop for Mqtt {
    fn drop(&mut self) {
        self.worker.abort();
    }
}

async fn drive(mut event_loop: EventLoop, deliveries: mpsc::Sender<Delivery>) {
    // A connection failure ends this session. The bridge controls backoff,
    // republishes availability, and retries its unacknowledged ring queue.
    // Continuing poll() after an error would reconnect without that coordination.
    while let Ok(event) = event_loop.poll().await {
        let delivery = match event {
            Event::Outgoing(Outgoing::Publish(id)) => Delivery::Sent(id),
            Event::Incoming(Incoming::PubAck(ack)) => Delivery::Acknowledged(ack.pkid),
            _ => continue,
        };
        if deliveries.send(delivery).await.is_err() {
            break;
        }
    }
}
