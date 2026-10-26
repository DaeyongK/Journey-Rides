function doPost(e) {
  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var payload = JSON.parse(e.postData.contents);
    
    var action = payload.action || "add"; 
    var aid = payload.announcement_id;
    var content = payload.content_category;
    var sheetName = "";
    
    // Target the correct sheet based on category
    if (content === "F") { 
      sheetName = "Friday PM Imports";
    } else if (content === "S") {
      sheetName = "Sunday Service Imports";
    } else if (content === "E") {
      sheetName = "Special Event Imports";
    } else {
      return ContentService.createTextOutput("Error: Invalid content category.");
    }
   
    var sheet = ss.getSheetByName(sheetName);
    if (!sheet) return ContentService.createTextOutput("Error: No sheet found named " + sheetName);

    // ==========================================
    // AUTO-RESET & MEMORY LOGIC 
    // ==========================================
    var props = PropertiesService.getDocumentProperties();
    var memoryKey = "active_aid_" + content; 
    var savedAid = props.getProperty(memoryKey);

    // TRIGGERED BY PYTHON ON SEND_AT: Wipe sheet and memorize new ID
    if (action === "reset") {
      sheet.clear(); 
      props.setProperty(memoryKey, aid); 
      return ContentService.createTextOutput("✅ Sheet wiped and tracking new ID: " + aid);
    }

    // If someone clicks a button on an old announcement, ignore it.
    if (savedAid && savedAid !== aid) {
      return ContentService.createTextOutput("⚠️ Ignored: This announcement is no longer active.");
    }

    // Calculates column placements for data
    var rowNum = parseInt(payload.count);
    if (rowNum < 1) rowNum = 1; 

    var school = payload.school; 
    var role = payload.role.toLowerCase(); 
    var schools = ["GT", "Emory", "GSU"];
    
    var schoolIndex = schools.indexOf(school);
    if (schoolIndex === -1) schoolIndex = 0; // Fallback to GT if something goes weird
    
    // Column layout per school block (1-based offsets from startIndex):
    //   Friday PM / Special Event (8 wide):
    //     driver: name, seats, phone, info | rider: name, phone, info | blank
    //   Sunday Service (10 wide, adds F/R = Food or Return to campus):
    //     driver: name, seats, phone, info, F/R | rider: name, phone, info, F/R | blank
    var hasFR = content === "S";
    var driverWidth = hasFR ? 5 : 4;
    var riderWidth = hasFR ? 4 : 3;
    var blockWidth = driverWidth + riderWidth + 1;

    var startIndex = schoolIndex * blockWidth;

    // Withdrawal logic
    if (action === "delete") {
      if (role === "driver") {
        sheet.getRange(rowNum, startIndex + 1, 1, driverWidth).clearContent();
      } else {
        sheet.getRange(rowNum, startIndex + driverWidth + 1, 1, riderWidth).clearContent();
      }
      return ContentService.createTextOutput("✅ Cleared row " + rowNum);
    }

    // Same-day withdrawal: keep the row, just mark the note cell "WITHDRAWN"
    if (action === "withdraw_note") {
      var noteCol = role === "driver" ? startIndex + 4 : startIndex + driverWidth + 3;
      sheet.getRange(rowNum, noteCol, 1, 1).setValue("WITHDRAWN");
      return ContentService.createTextOutput("✅ Marked row " + rowNum + " WITHDRAWN");
    }
    
    // Sign up logic
    if (action === "add") {
      var fr = payload.after_service || "";
      if (role === "driver") {
        var driverRow = [payload.name, payload.seats, payload.phone, payload.info];
        if (hasFR) driverRow.push(fr);
        sheet.getRange(rowNum, startIndex + 1, 1, driverWidth).setValues([driverRow]);
      } else {
        var riderRow = [payload.name, payload.phone, payload.info];
        if (hasFR) riderRow.push(fr);
        sheet.getRange(rowNum, startIndex + driverWidth + 1, 1, riderWidth).setValues([riderRow]);
      }
      return ContentService.createTextOutput("✅ Success adding to row " + rowNum);
    }
    
  } catch (error) {
    return ContentService.createTextOutput("Apps Script Error: " + error.message);
  }
}