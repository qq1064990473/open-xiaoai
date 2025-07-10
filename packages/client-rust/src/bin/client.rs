use open_xiaoai::services::audio::config::AudioConfig;
use open_xiaoai::services::monitor::kws::KwsMonitor;
use serde::de::value;
use open_xiaoai::services::monitor::instruction::{InstructionMonitor, Payload, LogMessage};
use open_xiaoai::services::monitor::file::FileMonitorEvent;
use open_xiaoai::services::speaker::SpeakerManager;
use serde_json::json;
use std::time::Duration;
use tokio::time::sleep;
use tokio_tungstenite::connect_async;
use tokio::process::Command;
use reqwest::Error;

use open_xiaoai::base::AppError;
use open_xiaoai::base::VERSION;
use open_xiaoai::services::audio::play::AudioPlayer;
use open_xiaoai::services::audio::record::AudioRecorder;
use open_xiaoai::services::connect::data::{Event, Request, Response, Stream};
use open_xiaoai::services::connect::handler::MessageHandler;
use open_xiaoai::services::connect::message::{MessageManager, WsStream};
use open_xiaoai::services::connect::rpc::RPC;
//use open_xiaoai::services::monitor::instruction::InstructionMonitor;
use open_xiaoai::services::monitor::playing::PlayingMonitor;

struct AppClient;

impl AppClient {
    pub async fn connect(url: &str) -> Result<WsStream, AppError> {
        let (ws_stream, _) = connect_async(url).await?;
        Ok(WsStream::Client(ws_stream))
    }

    pub async fn run() {
        let url = std::env::args().nth(1).expect("❌ 请输入服务器地址");
        //let url = "ws://127.0.0.1:4399";
        println!("✅ 已启动");
        loop {
            let Ok(ws_stream) = AppClient::connect(&url).await else {
                sleep(Duration::from_secs(1)).await;
                continue;
            };
            println!("✅ 已连接: {:?}", url);
            AppClient::init(ws_stream).await;
            if let Err(e) = MessageManager::instance().process_messages().await {
                eprintln!("❌ 消息处理异常: {}", e);
            }
            AppClient::dispose().await;
            eprintln!("❌ 已断开连接");
        }
    }

    async fn init(ws_stream: WsStream) {
        MessageManager::instance().init(ws_stream).await;
        MessageHandler::<Event>::instance()
            .set_handler(on_event)
            .await;
        MessageHandler::<Stream>::instance()
            .set_handler(on_stream)
            .await;

        let rpc = RPC::instance();
        rpc.add_command("get_version", get_version).await;
        rpc.add_command("run_shell", run_shell).await;
        rpc.add_command("start_play", start_play).await;
        rpc.add_command("stop_play", stop_play).await;
        rpc.add_command("start_recording", start_recording).await;
        rpc.add_command("stop_recording", stop_recording).await;


        // InstructionMonitor::start(|event| async move {
        //     MessageManager::instance()
        //         .send_event("instruction", Some(json!(event)))
        //         .await
        // })
        // .await;

InstructionMonitor::start(|event: FileMonitorEvent| async move {
    // 发送原始日志事件
    let raw_value = match &event {
        FileMonitorEvent::NewFile => json!("NewFile"),
        FileMonitorEvent::NewLine(line) => json!(line),
    };

    MessageManager::instance()
        .send_event("instruction", Some(raw_value))
        .await
        .ok();

    // 只处理 NewLine 类型
    if let FileMonitorEvent::NewLine(line) = event {
        match serde_json::from_str::<LogMessage>(&line) {
            Ok(log_msg) => {
                println!("📥 解析成功: {:?}", log_msg.header.name);

                if log_msg.header.name == "RecognizeResult" {
                    if let Payload::RecognizeResultPayload { is_final, results, .. } = log_msg.payload {
                        if is_final {
                            for result in results {
                                println!("🔍 is_final = true, 文本: {}", result.text);
                                if let Some(song) = extract_song_name(&result.text) {
                                    println!("🎵 播放歌曲名: {}", song);
                                    AppClient::play_song_by_name(&song).await;
                                }
                            }
                        } else {
                            println!("⏭️ is_final = false，跳过播放");
                        }
                    } else {
                        println!("⚠️ payload 不是 RecognizeResultPayload");
                    }
                }
            }
            Err(e) => {
                eprintln!("❌ 无法解析为 LogMessage: {}", e);
                eprintln!("📜 原始行: {}", line);
            }
        }
    }

    Ok(())
}).await;

        PlayingMonitor::start(|event| async move {
            MessageManager::instance()
                .send_event("playing", Some(json!(event)))
                .await
        })
        .await;

        KwsMonitor::start(|event| async move {
            MessageManager::instance()
                .send_event("kws", Some(json!(event)))
                .await
        })
        .await;
    }


    async fn dispose() {
        MessageManager::instance().dispose().await;
        let _ = AudioPlayer::instance().stop().await;
        let _ = AudioRecorder::instance().stop_recording().await;
        InstructionMonitor::stop().await;
        PlayingMonitor::stop().await;
        KwsMonitor::stop().await;
    }

 async fn play_song_by_name(song_name: &str) {
        println!("🎶 尝试播放歌曲: {}", song_name);

        let search_url = format!(
            "https://musicapi.haitangw.net/music/?qq&name={}",
            song_name
        );

        match reqwest::get(&search_url).await {
            Ok(resp) => match resp.json::<SearchResponse>().await {
                Ok(search_resp) => {
                    if let Some(song) = search_resp.data.get(0) {
                        let rid = &song.rid;
                        let play_url_api = format!(
                            "https://musicapi.haitangw.net/music/qq_song_kw.php?id={}",
                            rid
                        );

                        if let Ok(play_resp) = reqwest::get(&play_url_api).await {
                            if let Ok(play_info) = play_resp.json::<PlayResponse>().await {
                                if let Some(url) = play_info.data.url {
                                    println!("▶️ 开始播放: {}", url);

                                    let shell_command = format!(
                                        "ubus -t 1 call mediaplayer player_play_url '{{\"url\":\"{}\",\"type\":1}}'",
                                        url
                                    );

                                    match run_shell_with_command(&shell_command).await {
                                        Ok(output) => println!("✅ 播放命令返回: {:?}", output),
                                        Err(e) => eprintln!("❌ 播放命令异常: {}", e),
                                    }
                                }
                            }
                        }
                    } else {
                        eprintln!("❌ 未找到歌曲");
                    }
                }
                Err(e) => eprintln!("❌ 搜索结果解析失败: {}", e),
            },
            Err(e) => eprintln!("❌ 搜索请求失败: {}", e),
        }
    }
}


fn extract_song_name(text: &str) -> Option<String> {
    let prefix = "播放歌曲";
    if text.starts_with(prefix) {
        Some(text[prefix.len()..].trim().to_string())
    } else {
        None
    }
}

async fn get_version(_: Request) -> Result<Response, AppError> {
    let data = json!(VERSION.to_string());
    Ok(Response::from_data(data))
}

async fn start_play(request: Request) -> Result<Response, AppError> {
    let config = request
        .payload
        .and_then(|payload| serde_json::from_value::<AudioConfig>(payload).ok());
    AudioPlayer::instance().start(config).await?;
    Ok(Response::success())
}

async fn stop_play(_: Request) -> Result<Response, AppError> {
    AudioPlayer::instance().stop().await?;
    Ok(Response::success())
}

async fn start_recording(request: Request) -> Result<Response, AppError> {
    let config = request
        .payload
        .and_then(|payload| serde_json::from_value::<AudioConfig>(payload).ok());
    AudioRecorder::instance()
        .start_recording(
            |bytes| async {
                MessageManager::instance()
                    .send_stream("record", bytes, None)
                    .await
            },
            config,
        )
        .await?;
    Ok(Response::success())
}

async fn stop_recording(_: Request) -> Result<Response, AppError> {
    AudioRecorder::instance().stop_recording().await?;
    Ok(Response::success())
}

async fn run_shell(request: Request) -> Result<Response, AppError> {
    let script = match request.payload {
        Some(payload) => serde_json::from_value::<String>(payload)?,
        _ => return Err("empty command".into()),
    };
    let res = open_xiaoai::utils::shell::run_shell(script.as_str()).await?;
    Ok(Response::from_data(json!(res)))
}

async fn on_event(event: Event) -> Result<(), AppError> {
    println!("🔥 收到事件: {:?}", event);
    Ok(())
}

async fn run_shell_with_command(script: &str) -> Result<serde_json::Value, AppError> {
    let output = Command::new("sh")
        .arg("-c")
        .arg(script)
        .output()
        .await?;

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();

    Ok(json!({
        "stdout": stdout,
        "stderr": stderr,
        "status": output.status.code()
    }))
}

async fn on_stream(stream: Stream) -> Result<(), AppError> {
    let Stream { tag, bytes, .. } = stream;
    match tag.as_str() {
        "play" => {
            // 播放接收到的音频流
            let _ = AudioPlayer::instance().play(bytes).await;
        }
        _ => {}
    }
    Ok(())
}

#[derive(Debug, serde::Deserialize)]
struct SearchResponse {
    code: u32,
    msg: String,
    data: Vec<SongData>,
}

#[derive(Debug, serde::Deserialize)]
struct SongData {
    rid: String,
    mid: String,
    name: String,
    artist: String,
    album: String,
    pic: String,
    duration: String,
    quality: Vec<Quality>,
}

#[derive(Debug, serde::Deserialize)]
struct Quality {
    size: String,
    quality: String,
    level: String,
}

#[derive(Debug, serde::Deserialize)]
struct PlayResponse {
    code: u32,
    msg: String,
    data: PlayData,
}

#[derive(Debug, serde::Deserialize)]
struct PlayData {
    rid: String,
    media_mid: String,
    name: String,
    artist: String,
    album: String,
    error: String,
    quality: String,
    size: String,
    pic: String,
    url: Option<String>,
    lrc: Option<String>,
}

#[tokio::main]
async fn main() {
    AppClient::run().await;
}
