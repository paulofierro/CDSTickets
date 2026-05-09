#!/usr/bin/env swift
import Foundation

// MARK: - Constants

let base = "https://ticket.cds.ky"
let listURL = URL(string: "\(base)/event?category=2")!
let bookingURL = URL(string: "\(base)/booking")!
let seatsURL = URL(string: "\(base)/GetNoOfTickets")!
let category = "2"

// MARK: - Models

struct ShowInfo: Codable {
    let id: Int
    let date: String
    let time: String?
    let seatsLeft: Int?
    let seatsSold: Int?
}

struct EventInfo: Codable {
    let id: Int
    let title: String?
}

struct Payload: Codable {
    let event: EventInfo
    let shows: [ShowInfo]
}

// MARK: - HTTP

let session: URLSession = {
    let config = URLSessionConfiguration.ephemeral
    config.httpShouldSetCookies = true
    config.httpCookieAcceptPolicy = .always
    return URLSession(configuration: config)
}()

func get(_ url: URL, headers: [String: String] = [:]) async throws -> String {
    var req = URLRequest(url: url)
    for (k, v) in headers { req.setValue(v, forHTTPHeaderField: k) }
    let (data, _) = try await session.data(for: req)
    return String(decoding: data, as: UTF8.self)
}

func post(_ url: URL, fields: [(String, String)], headers: [String: String] = [:]) async throws -> String {
    var req = URLRequest(url: url)
    req.httpMethod = "POST"
    var comps = URLComponents()
    comps.queryItems = fields.map { URLQueryItem(name: $0.0, value: $0.1) }
    req.httpBody = (comps.percentEncodedQuery ?? "").data(using: .utf8)
    req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
    for (k, v) in headers { req.setValue(v, forHTTPHeaderField: k) }
    let (data, _) = try await session.data(for: req)
    return String(decoding: data, as: UTF8.self)
}

// MARK: - Regex helpers (NSRegularExpression for broad compatibility)

func firstMatch(_ pattern: String, in text: String) -> [String]? {
    guard let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]) else { return nil }
    let range = NSRange(text.startIndex..., in: text)
    guard let m = regex.firstMatch(in: text, range: range) else { return nil }
    return (0..<m.numberOfRanges).map { i in
        Range(m.range(at: i), in: text).map { String(text[$0]) } ?? ""
    }
}

func allMatches(_ pattern: String, in text: String) -> [[String]] {
    guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }
    let range = NSRange(text.startIndex..., in: text)
    return regex.matches(in: text, range: range).map { m in
        (0..<m.numberOfRanges).map { i in
            Range(m.range(at: i), in: text).map { String(text[$0]) } ?? ""
        }
    }
}

// MARK: - Parsers

func extractCSRF(_ html: String) -> String {
    guard let groups = firstMatch(#"csrf_cds_value\s*=\s*"([^"]+)""#, in: html), groups.count > 1 else {
        fatalError("CSRF token not found")
    }
    return groups[1]
}

func extractEventId(_ html: String) -> String {
    guard let groups = firstMatch(#"name="EventId"\s+value="(\d+)""#, in: html), groups.count > 1 else {
        fatalError("EventId not found on listing page")
    }
    return groups[1]
}

func extractEventTitle(_ html: String) -> String? {
    firstMatch(#"title="([^"]+)"\s+href="[^"]+"\s+class="link">[^<]+</a></h4>"#, in: html)?
        .dropFirst().first
}

func extractDates(_ html: String) -> [(id: String, label: String)] {
    allMatches(#"<option value="(\d+)" data-subtext="">([^<]+)</option>"#, in: html)
        .compactMap { groups in
            guard groups.count > 2 else { return nil }
            return (id: groups[1], label: groups[2].trimmingCharacters(in: .whitespaces))
        }
}

func parseLabel(_ label: String) -> (date: String, time: String?) {
    if let groups = firstMatch(#"^(.*?)\s*\(\s*(.*?)\s*\)\s*$"#, in: label), groups.count > 2 {
        return (groups[1].trimmingCharacters(in: .whitespaces),
                groups[2].trimmingCharacters(in: .whitespaces))
    }
    return (label, nil)
}

func parseSeats(_ html: String) -> (left: Int?, sold: Int?) {
    let sold = firstMatch(#"name="PaidShowSeat"[^>]*value="(\d+)""#, in: html)?
        .dropFirst().first.flatMap { Int($0) }

    if let groups = firstMatch(#"Only\s+(\d+)\s+Seats?\s+Are\s+Left"#, in: html),
       groups.count > 1, let left = Int(groups[1]) {
        return (left, sold)
    }

    if firstMatch(#"name="bookingfull"[^>]*value="yes""#, in: html) != nil ||
       firstMatch(#"sold\s*out"#, in: html) != nil {
        return (0, sold)
    }

    // Fallback: HidTotalSeat - PaidShowSeat
    if let groups = firstMatch(#"name="HidTotalSeat"[^>]*value="(\d+)""#, in: html),
       groups.count > 1, let total = Int(groups[1]), let sold = sold {
        return (total - sold, sold)
    }

    return (nil, sold)
}

// MARK: - Main

func warn(_ s: String) {
    FileHandle.standardError.write(Data((s + "\n").utf8))
}

func run() async throws {
    let listing = try await get(listURL, headers: ["User-Agent": "Mozilla/5.0"])
    var csrf = extractCSRF(listing)
    let eventId = extractEventId(listing)
    let title = extractEventTitle(listing)
    warn("Event: \(title ?? "?") (id=\(eventId))")

    let booking = try await post(
        bookingURL,
        fields: [("csrf_srm", csrf), ("EventId", eventId), ("EventRegType", "Y")],
        headers: [
            "User-Agent": "Mozilla/5.0",
            "Referer": listURL.absoluteString,
        ]
    )
    csrf = extractCSRF(booking)  // CSRF rotates per page load
    let dates = extractDates(booking)
    warn("Found \(dates.count) dates")

    var shows: [ShowInfo] = []
    for date in dates {
        let parsed = parseLabel(date.label)
        let resp = try await post(
            seatsURL,
            fields: [
                ("csrf_srm", csrf),
                ("EventID", eventId),
                ("EventDate", date.id),
                ("catdata", category),
            ],
            headers: [
                "User-Agent": "Mozilla/5.0",
                "Referer": bookingURL.absoluteString,
                "X-Requested-With": "XMLHttpRequest",
            ]
        )
        let seats = parseSeats(resp)
        shows.append(ShowInfo(
            id: Int(date.id) ?? 0,
            date: parsed.date,
            time: parsed.time,
            seatsLeft: seats.left,
            seatsSold: seats.sold
        ))
        let l = seats.left.map(String.init) ?? "nil"
        let s = seats.sold.map(String.init) ?? "nil"
        warn("  \(parsed.date) \(parsed.time ?? ""): left=\(l), sold=\(s)")
    }

    let payload = Payload(event: EventInfo(id: Int(eventId) ?? 0, title: title), shows: shows)
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted]
    encoder.keyEncodingStrategy = .convertToSnakeCase
    let data = try encoder.encode(payload)
    try data.write(to: URL(fileURLWithPath: "seats.json"))
    warn("Wrote seats.json")
}

try await run()
